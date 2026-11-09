# Weak Network Simulator (弱网模拟工具)

基于 WinDivert 的弱网模拟工具，提供 Web 可视化界面，支持三种测试场景。

## 功能

| 场景 | 说明 | 可调参数 |
|------|------|----------|
| **弱网模拟** | 模拟延迟、丢包、带宽限制 | 延迟(0~5000ms)、丢包率(0~100%)、带宽(0~1000KB/s) |
| **网络切换** | 模拟WiFi→4G等切换过程 | 断开时长、重连延迟、重连丢包率 |
| **断网重连** | 完全断网后自动恢复 | 断网时长(1~60s) |

## 快速开始

### 环境要求
- Windows 10/11
- Python 3.8+
- **管理员权限**（WinDivert 需要）

### 安装运行

**方式一：双击启动**
```
双击 start.bat（自动请求管理员权限并安装依赖）
```

**方式二：手动启动**
```bash
pip install -r requirements.txt
# 以管理员身份运行命令行
python app.py
# 浏览器打开 http://127.0.0.1:5000
```

## 技术栈
- **后端**: Python + Flask + pydivert (WinDivert Python wrapper)
- **前端**: 原生 HTML/CSS/JS，深色主题
- **核心**: WinDivert 内核驱动拦截网络数据包

## 过滤规则示例

| 规则 | 说明 |
|------|------|
| `true` | 所有流量 |
| `outbound` | 所有出站流量 |
| `outbound and loopback` | 本机回环 (localhost) |
| `tcp and outbound` | TCP 出站 |
| `ip.DstAddr == 1.2.3.4` | 指定 IP |
| `tcp.DstPort == 80` | 指定端口 |

## 注意事项
- 必须以**管理员权限**运行
- 测试 localhost 时过滤规则必须包含 `outbound`
- 停止模拟后网络自动恢复正常

## License
MIT
