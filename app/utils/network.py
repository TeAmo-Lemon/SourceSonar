# 本文件用于提供网络相关工具函数，例如获取本机局域网 IP 地址。
from __future__ import annotations
import socket
from typing import Optional

# 输入: 无
# 输出: 本机局域网 IP 地址字符串；获取失败时返回 None
# 作用: 通过 UDP 连接外部地址探测本机出口网卡 IP，用于打印局域网访问网址
def get_lan_ip() -> Optional[str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('8.8.8.8', 80))
            return sock.getsockname()[0]
    except OSError:
        return None
