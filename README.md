# AniCat-v2

AniCat-v2 為一個 [Anime1.me](https://anime1.me/) 的下載器。

## 功能
- 支援多連結輸入
- 支援下載進度條

## 使用方法

1. 建立環境
    
    ```
    pip3 install -r requirements.txt 
    ```

2. 執行 Python

    ```
    python3 anime1.py 
    ```

3. 輸入 [Anime1.me](https://anime1.me/) 的動畫連結

    - 支援的連結格式
        
        - 單季連結：`https://anime1.me/category/...`
        - 單集連結：：`https://anime1.me/...`
        - 範例
        
            ```
            ? Anime1 URL：https://anime1.me/category/2021年冬季/關於我轉生變成史萊姆這檔事-第二季
            ? Anime1 URL：https://anime1.me/15651
            ```
    - 支援多連結
        - 連結間以 `,` 區隔
        - 範例
            
            ```
            ? Anime1 URL：https://anime1.me/15651,https://anime1.me/15603
            ? Anime1 URL：https://anime1.me/15651,https://anime1.me/category/2021年冬季/關於我轉生變成史萊姆這檔事-第二季
            ```

## TODO
- [ ] 平行化下載
- [ ] GUI 介面
