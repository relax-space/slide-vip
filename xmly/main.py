import asyncio
import io
import json
import os
from typing import Dict, Tuple

import cv2
import requests
from PIL import Image
from pyppeteer import launch
from pyppeteer.page import Page


class Login():

    def __init__(self):
        pass

    def get_distance(self, bg_img_path: str, gp_img_path: str,
                     out: str) -> int:
        '''
        bg: 背景图片
        tp: 缺口图片
        out:输出图片
        '''
        # 读取背景图片和缺口图片
        bg_img = cv2.imread(bg_img_path)  # 背景图片
        tp_img = cv2.imread(gp_img_path)  # 缺口图片

        # 识别图片边缘
        bg_edge = cv2.Canny(bg_img, 100, 200)
        tp_edge = cv2.Canny(tp_img, 100, 200)

        # 转换图片格式
        bg_pic = cv2.cvtColor(bg_edge, cv2.COLOR_GRAY2RGB)
        tp_pic = cv2.cvtColor(tp_edge, cv2.COLOR_GRAY2RGB)

        # 缺口匹配
        res = cv2.matchTemplate(bg_pic, tp_pic, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)  # 寻找最优匹配

        # 绘制方框
        th, tw = tp_pic.shape[:2]
        tl = max_loc  # 左上角点的坐标
        br = (tl[0] + tw, tl[1] + th)  # 右下角点的坐标
        cv2.rectangle(bg_img, tl, br, (0, 0, 255), 2)  # 绘制矩形
        cv2.imwrite(out, bg_img)  # 保存在本地

        # 返回缺口的X坐标
        return tl[0]

    def slide_list(self, total_length: int):
        '''
        拿到移动轨迹，模仿人的滑动行为，先匀加速后匀减速
        匀变速运动基本公式：
        ①v=v0+at
        ②s=v0t+½at²
        ③v²-v0²=2as
        :param total_length: 需要移动的距离
        :return: 每段移动的距离列表
        '''
        # 初速度
        v = 0
        # 单位时间为0.3s来统计轨迹，轨迹即0.3内的位移
        t = 1
        # 位移/轨迹列表，列表内的一个元素代表一个T时间单位的位移,t越大，每次移动的距离越大
        slide_result = []
        # 当前的位移
        current = 0
        # 到达mid值开始减速
        mid = total_length * 4 / 5

        while current < total_length:
            if current < mid:
                # 加速度越小，单位时间的位移越小,模拟的轨迹就越多越详细
                a = 2
            else:
                a = -3
            # 初速度
            v0 = v
            # 0.2秒时间内的位移
            s = v0 * t + 0.5 * a * (t**2)
            # 当前的位置
            current += s
            # 添加到轨迹列表
            slide_result.append(round(s))

            # 速度已经达到v,该速度作为下次的初速度
            v = v0 + a * t
        return slide_result

    async def is_success(self, page: Page):
        '''
        如果出现用户名, 则表示登录成功
        '''
        el_href = await page.Jeval('.user-notice._XF', 'el=>el.href')
        if el_href != 'https://www.ximalaya.com/my/':
            return False
        return True

    async def is_sms(self, page: Page):
        return await page.J('[class="sms-pop__form-title _tl"]')

    def resize_img(self, img):
        (x, y) = img.size
        x_resize = int(x / 1.25)
        y_resize = int(y / 1.25)
        img = img.resize((x_resize, y_resize), Image.ANTIALIAS)
        return img

    async def pass_slider(self, bg_img_path: str, gp_img_path: str,
                          out_path: str, retry_count: int, page: Page,
                          ua: str):
        if retry_count == 0:
            return False
        retry_count -= 1
        # 背景图片
        bg_url = await page.Jeval(
            '#__xmca-container > div.__xmca-wrapper > div.__xmca-body > img.__xmca-img-main',
            'el => el.src')
        # 缺口图片
        gp_url = await page.Jeval('#__xmca-img-bl', 'el=>el.src')

        headers = {'user-agent': ua, 'referer': 'https://www.ximalaya.com/'}
        bg_file = io.BytesIO(requests.get(bg_url, headers=headers).content)
        bg_im = Image.open(bg_file)
        # 重新设置图片大小, 因为下载下来的是实际图片, 但是网页上会有一定缩放
        bg_im = self.resize_img(bg_im)
        bg_im.save(bg_img_path)

        tp_file = io.BytesIO(requests.get(gp_url, headers=headers).content)
        tp_im = Image.open(tp_file)
        tp_im = self.resize_img(tp_im)
        tp_im.save(gp_img_path)

        # 缺口距离左边有10像素距离
        distance = self.get_distance(bg_img_path, gp_img_path, out_path) + 5
        await page.hover('#__xmca-block')
        x = page.mouse._x
        await page.mouse.down()
        distance_list = self.slide_list(distance)
        for d in distance_list:
            x += d
            await page.mouse.move(x, 0)
        await page.mouse.up()
        await page.waitFor(2000)
        success = await self.is_success(page)
        if not success:
            count = 3 - retry_count
            print(f"登录第{count}次重试,睡6秒...")
            if await self.is_sms(page):
                await page.click('.sms-btn._tl')
                code = input("请输入手机验证码：")
                print(code)
                if not code:
                    return False
                await page.type(
                    '.sms-pop__form-code-input.xm-input__inner._tl', code)
                await page.click('.sms-code-btn.btn-confirm._tl')
                await page.waitFor(2000)
                return await self.is_success(page)
            # 如果没有成功, 则等6秒,然后刷新图片重试
            await page.waitFor(6000)
            await page.click('#__xmca-refresh')
            await page.waitFor(2000)
            await self.pass_slider(bg_img_path, gp_img_path, out_path,
                                   retry_count, page, ua)
            return False
        return True

    def get_cookie(self, user_name: str) -> Dict:
        file_path = f'account/{user_name}.json'
        is_exist = os.path.isfile(file_path)
        if not is_exist:
            return None
        with open(file_path, mode='r', encoding='utf-8') as f:
            return json.load(f)

    def set_cookie(self, user_name: str, contents: Dict):
        file_path = f'account/{user_name}.json'
        with open(file_path, mode='w', encoding='utf-8') as f:
            json.dump(contents, f)

    async def is_success_req(self, host: str, cookie_dict: Dict, ua: str):
        # https://www.ximalaya.com/revision/main/getCurrentUser
        url = f'{host}/revision/main/getCurrentUser'
        headers = {
            'User-Agent': ua,
        }

        resp = requests.get(url, headers=headers, cookies=cookie_dict)
        if not resp or resp.json()['ret'] != 200:
            return False
        return True

    async def login(self, host: str, name: str, pwd: str, page: Page, ua: str):
        await asyncio.gather(page.goto(host), page.waitForNavigation())

        await page.click(
            '#rootHeader > div > div.xui-header-iconNav._uH > div > div > img')
        await page.waitFor(1000)
        await page.type('#accountName', name)
        await page.type('#accountPWD', pwd)
        await page.click('.login-pop__form > div:nth-child(3) > button')

        # # 手动拖到滑块
        # await page.waitFor(20000)
        # return True
        await page.waitFor(2000)
        success = await self.is_success(page)
        if not success:
            return await self.pass_slider('data/1.png', 'data/2.png',
                                          'data/x.png', 3, page, ua)
        return True

    async def login_cache(self, host: str, name: str, pwd: str, ua: str,
                          page: Page) -> Tuple[Dict, bool]:
        cookie_dict = self.get_cookie(name)
        if cookie_dict:
            is_success = await self.is_success_req(host, cookie_dict, ua)
            if is_success:
                print('登录成功 通过cookie')
                return cookie_dict, True

        is_success = await self.login(host, name, pwd, page, ua)
        if is_success:
            print('登录成功 通过账号')
            cookies_raw = await page.cookies()
            cookies_dict = {v['name']: v['value'] for v in cookies_raw}
            self.set_cookie(name, cookies_dict)
            return cookies_dict, True
        return None, False


def screen_size():
    """使用tkinter获取屏幕大小"""
    try:
        import tkinter
        tk = tkinter.Tk()
        width = tk.winfo_screenwidth()
        height = tk.winfo_screenheight()
        tk.quit()
        return {'width': width, 'height': height}
    except Exception:
        return {'width': 1366, 'height': 768}


async def get_page_notrace(ua) -> Page:
    browser = await launch({
        'headless': True,
        'executablePath': os.getenv('chromium_drive'),
        'args': ['--no-sandbox', '--disable-gpu'],
        'dumpio': True
    })
    context = await browser.createIncognitoBrowserContext()
    page = await context.newPage()
    await page.setViewport(screen_size())
    await page.setUserAgent(ua)
    return page


def init_folder():
    if not os.path.isdir('data'):
        os.makedirs('data')
    if not os.path.isdir('account'):
        os.makedirs('account')


async def main(host: str):
    name = 'xxx'
    pwd = 'xxx'

    init_folder()
    # 修改工作空间
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_8_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/32.0.1664.3 Safari/537.36'
    page = await get_page_notrace(ua)
    cookie_dict, success = await Login().login_cache(host, name, pwd, ua, page)
    print(cookie_dict, success)


if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(
        main('https://www.ximalaya.com'))
