#!/usr/bin/python
# -*- coding: UTF-8 -*-

import requests,os,sys,re
import datetime, json
from bs4 import BeautifulSoup
Cookies = None
download_path = os.getcwd() + "/Anime1_Download"

# 設定 Header 
headers = {
    "Accept": "*/*",
    "Accept-Language": 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    "DNT": "1",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "cookie": "__cfduid=d8db8ce8747b090ff3601ac6d9d22fb951579718376; _ga=GA1.2.1940993661.1579718377; _gid=GA1.2.1806075473.1579718377; _ga=GA1.3.1940993661.1579718377; _gid=GA1.3.1806075473.1579718377",
    "Content-Type":"application/x-www-form-urlencoded",
    "user-agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/71.0.3573.0 Safari/537.36",
    }


def Anime_Unit(url):
    #1 https://anime1.me/14323
    r = requests.post(url,headers = headers)
    soup = BeautifulSoup(r.text, 'lxml') 
    url = soup.find('iframe').get('src')
    title = soup.find('h1', class_="entry-title").text
    # print(title)

    #2 https://v.anime1.me/watch?v=KRPeC
    r = requests.post(url,headers = headers)
    soup = BeautifulSoup(r.text, 'lxml') 
    script_text = soup.find_all("script")[1].string
    xsend = 'd=' + re.search(r"'d=(.*?)'", script_text, re.M|re.I).group(1)

    #3 APIv2
    r = requests.post('https://v.anime1.me/api',headers = headers,data = xsend)
    url = 'https:' + json.loads(r.text)['l']
    
    global Cookies
    set_cookie = r.headers['set-cookie']
    cookie_e = 'e=' + re.search(r"e=(.*?);", set_cookie, re.M|re.I).group(1) + ';'
    cookie_p = 'p=' + re.search(r"p=(.*?);", set_cookie, re.M|re.I).group(1) + ';'
    cookie_h = 'h=' + re.search(r"HttpOnly, h=(.*?);", set_cookie, re.M|re.I).group(1) + ';'
    Cookies = cookie_e + cookie_p + cookie_h
    # print(Cookies)
    MP4_DL(url, title)

def MP4_DL(Doenload_URL, Video_Name):

    headers_cookies ={
        "accept": "*/*",
        "accept-encoding": 'identity;q=1, *;q=0',
        "accept-language": 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        "cookie": Cookies,
        "dnt": '1',
        "user-agent": 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36'
    }

    r = requests.get(Doenload_URL,headers = headers_cookies) 
    with open(os.path.join(download_path,  Video_Name + ".mp4"), 'wb') as f:
        f.write(r.content)
        f.flush()
        f.close()

    if(r.status_code == 200):
        print("\033[11D\033[0m",end= "", flush= True )
        print("\033[1;34mSuccess    \033[0m")  # 藍色成功
    else:
        print("\033[1;31mFailure    \033[0m")  # 紅色錯誤

if __name__ == '__main__': 
    if not os.path.exists(download_path):
        os.mkdir(download_path)

    Anime_Unit("https://anime1.me/15606")
