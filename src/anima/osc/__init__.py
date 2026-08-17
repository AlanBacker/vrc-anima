"""OSC 层:与 VRChat 的唯一控制通道。

- client:发送 /input 轴与按键(带看门狗)、聊天框、panic
- listener:接收 9001 端口的自身状态输出
- chatbox:144 字符分条 + 漏桶限速的聊天框镜像
"""
