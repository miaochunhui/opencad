"""cadcore 测试包（G6 判定行要求 `python3 -m unittest discover -s tests -t .` 可发现）。

为什么要这个文件：`unittest discover -s tests -t .` 在 Python 3.10 下会把 start dir
当作 importable package 校验，缺 `__init__.py` 直接抛
`ImportError: Start directory is not importable` ⇒ G6 报红但一条测试都没跑
（"没算"伪装成"没失败"）。空包体即可，不放任何测试逻辑。
"""
