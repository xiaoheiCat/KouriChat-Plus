"""
企业微信平台接入模块
通过企业微信官方 API 接收和发送消息，完全跨平台，无需 Windows 依赖。

接入流程：
1. 企业微信管理后台创建自建应用，获取 corp_id / corp_secret / agent_id
2. 配置接收消息的 API，设置 callback_token 和 callback_aes_key
3. 启动后，企业微信会将用户消息以加密 XML 形式 POST 到回调 URL
4. 本模块解密消息 → 调用 AI 处理 → 通过企业微信 API 发送回复
"""

import hashlib
import logging
import json
import struct
import threading
import time
import base64
import os
from datetime import datetime

import requests
from flask import Flask, request, make_response
from Crypto.Cipher import AES

logger = logging.getLogger('main')


class WeCom:
    """
    企业微信平台封装。

    职责：
    - 启动 Flask Webhook 服务，接收企业微信回调
    - 验证签名、解密消息
    - 通过企业微信 REST API 发送回复
    - 维护 access_token 缓存（7200s 有效期，提前 60s 刷新）
    """

    def __init__(self, corp_id: str, corp_secret: str, agent_id: str,
                 callback_token: str, callback_aes_key: str,
                 port: int = 8081, callback_path: str = '/wecom/callback',
                 enable_markdown: bool = False):
        self.corp_id = corp_id
        self.corp_secret = corp_secret
        self.agent_id = agent_id
        self.token = callback_token
        self.aes_key = base64.b64decode(callback_aes_key + '=')  # 43字符Base64 → 32字节
        self.port = port
        self.callback_path = callback_path
        self.enable_markdown = enable_markdown

        # access_token 缓存
        self._access_token = ''
        self._token_expires_at = 0
        self._token_lock = threading.Lock()

        # 消息去重（60s 窗口）
        self._seen_msg_ids: dict[str, float] = {}
        self._dedup_lock = threading.Lock()

        # 外部注入的消息回调：fn(user_id, content, msg_type, media_id)
        self._message_handler = None

        self._app = Flask(__name__)
        self._app.add_url_rule(
            self.callback_path,
            'wecom_callback',
            self._callback_view,
            methods=['GET', 'POST']
        )

    # ------------------------------------------------------------------ #
    #  公开接口
    # ------------------------------------------------------------------ #

    def set_message_handler(self, handler):
        """注册消息回调函数：handler(user_id: str, content: str, msg_type: str)"""
        self._message_handler = handler

    def start(self):
        """在后台线程中启动 Flask Webhook 服务（非阻塞）"""
        t = threading.Thread(
            target=lambda: self._app.run(host='0.0.0.0', port=self.port, use_reloader=False),
            name='WeCom-Webhook',
            daemon=True
        )
        t.start()
        logger.info(f"企业微信 Webhook 已启动，监听端口 {self.port}，路径 {self.callback_path}")

    def send_text(self, user_id: str, content: str):
        """向企业微信用户发送文本消息（自动分片，单片 ≤ 2000 字节）"""
        if not content:
            return
        for chunk in self._split_by_bytes(content, 2000):
            self._send_message(user_id, {
                'msgtype': 'text',
                'text': {'content': chunk},
            })

    def send_markdown(self, user_id: str, content: str):
        """向企业微信用户发送 Markdown 消息（仅企业微信内渲染，个人微信不支持）"""
        if not content:
            return
        for chunk in self._split_by_bytes(content, 2000):
            self._send_message(user_id, {
                'msgtype': 'markdown',
                'markdown': {'content': chunk},
            })

    def reply(self, user_id: str, content: str):
        """统一回复入口，根据 enable_markdown 决定消息类型"""
        if self.enable_markdown:
            self.send_markdown(user_id, content)
        else:
            self.send_text(user_id, content)

    def send_image(self, user_id: str, image_path: str):
        """向企业微信用户发送图片（先上传临时素材拿 media_id，再发送）"""
        try:
            media_id = self._upload_media(image_path, 'image')
            if media_id:
                self._send_message(user_id, {
                    'msgtype': 'image',
                    'image': {'media_id': media_id},
                })
        except Exception as e:
            logger.error(f"企业微信发送图片失败 path={image_path}: {e}")

    # ------------------------------------------------------------------ #
    #  Flask 路由
    # ------------------------------------------------------------------ #

    def _callback_view(self):
        q = request.args
        msg_signature = q.get('msg_signature', '')
        timestamp = q.get('timestamp', '')
        nonce = q.get('nonce', '')

        if request.method == 'GET':
            echostr = q.get('echostr', '')
            if not self._verify_signature(msg_signature, timestamp, nonce, echostr):
                logger.warning("企业微信 URL 验证签名失败")
                return make_response('forbidden', 403)
            try:
                plain = self._decrypt(echostr)
                logger.info("企业微信 URL 验证成功")
                return make_response(plain, 200)
            except Exception as e:
                logger.error(f"企业微信 URL 验证解密失败: {e}")
                return make_response('error', 500)

        # POST — 接收消息
        body = request.data
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(body)
            encrypt = root.findtext('Encrypt', '')
        except Exception as e:
            logger.error(f"解析加密消息 XML 失败: {e}")
            return make_response('bad request', 400)

        if not self._verify_signature(msg_signature, timestamp, nonce, encrypt):
            logger.warning("企业微信消息签名验证失败")
            return make_response('forbidden', 403)

        # 立即返回 200（企业微信要求 5 秒内响应）
        response = make_response('', 200)

        try:
            plain_xml = self._decrypt(encrypt)
            msg_root = ET.fromstring(plain_xml)

            msg_id = msg_root.findtext('MsgId', '')
            msg_type = msg_root.findtext('MsgType', '')
            from_user = msg_root.findtext('FromUserName', '')
            create_time = int(msg_root.findtext('CreateTime', '0'))

            # 去重检查
            if msg_id and self._is_duplicate(msg_id):
                logger.debug(f"跳过重复消息 MsgId={msg_id}")
                return response

            # 忽略重启前的历史消息（60s 内）
            if create_time and (time.time() - create_time) > 60:
                logger.debug(f"忽略过期消息 CreateTime={create_time}")
                return response

            if msg_type == 'text':
                content = msg_root.findtext('Content', '').strip()
                logger.info(f"[企业微信] 收到文本消息 from={from_user} len={len(content)}")
                if content and self._message_handler:
                    threading.Thread(
                        target=self._message_handler,
                        args=(from_user, content),
                        daemon=True
                    ).start()

            elif msg_type == 'image':
                media_id = msg_root.findtext('MediaId', '')
                logger.info(f"[企业微信] 收到图片消息 from={from_user}")
                if self._message_handler:
                    threading.Thread(
                        target=self._handle_media,
                        args=(from_user, media_id, 'image'),
                        daemon=True
                    ).start()

            elif msg_type == 'voice':
                media_id = msg_root.findtext('MediaId', '')
                logger.info(f"[企业微信] 收到语音消息 from={from_user}")
                if self._message_handler:
                    threading.Thread(
                        target=self._handle_media,
                        args=(from_user, media_id, 'voice'),
                        daemon=True
                    ).start()

            else:
                logger.debug(f"[企业微信] 忽略不支持的消息类型: {msg_type}")

        except Exception as e:
            logger.error(f"处理企业微信消息失败: {e}", exc_info=True)

        return response

    def _handle_media(self, user_id: str, media_id: str, media_type: str):
        """下载媒体文件后调用 handler"""
        try:
            data = self._download_media(media_id)
            logger.info(f"[企业微信] 已下载 {media_type} 媒体，大小 {len(data)} 字节")

            if media_type == 'image' and self._message_handler:
                # 保存图片到临时目录
                temp_dir = os.path.join(os.getcwd(), 'data', 'images', 'temp')
                os.makedirs(temp_dir, exist_ok=True)

                temp_path = os.path.join(temp_dir, f"{media_id}_{int(time.time())}.jpg")
                with open(temp_path, 'wb') as f:
                    f.write(data)

                # 调用消息处理器，传递图片路径
                threading.Thread(
                    target=self._message_handler,
                    args=(user_id, temp_path, 'image'),
                    daemon=True
                ).start()
        except Exception as e:
            logger.error(f"下载媒体失败: {e}")

    # ------------------------------------------------------------------ #
    #  API 发送
    # ------------------------------------------------------------------ #

    def _send_message(self, user_id: str, payload_extra: dict):
        """调用企业微信消息发送 API"""
        try:
            token = self._get_access_token()
            url = f'https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}'
            payload = {
                'touser': user_id,
                'agentid': self.agent_id,
                'safe': 0,
                **payload_extra,
            }
            resp = requests.post(url, json=payload, timeout=15)
            result = resp.json()
            if result.get('errcode') != 0:
                logger.error(f"企业微信发送消息失败: {result}")
            else:
                logger.debug(f"企业微信消息发送成功 to={user_id}")
        except Exception as e:
            logger.error(f"企业微信发送消息异常: {e}")

    def _get_access_token(self) -> str:
        """获取 access_token，带缓存自动刷新"""
        with self._token_lock:
            if self._access_token and time.time() < self._token_expires_at:
                return self._access_token

            url = (f'https://qyapi.weixin.qq.com/cgi-bin/gettoken'
                   f'?corpid={self.corp_id}&corpsecret={self.corp_secret}')
            resp = requests.get(url, timeout=15)
            data = resp.json()
            if data.get('errcode', 0) != 0:
                raise RuntimeError(f"获取 access_token 失败: {data}")

            self._access_token = data['access_token']
            self._token_expires_at = time.time() + data['expires_in'] - 60
            logger.debug(f"access_token 已刷新，有效期 {data['expires_in']}s")
            return self._access_token

    def _download_media(self, media_id: str) -> bytes:
        """下载临时媒体文件"""
        token = self._get_access_token()
        url = f'https://qyapi.weixin.qq.com/cgi-bin/media/get?access_token={token}&media_id={media_id}'
        resp = requests.get(url, timeout=30)
        return resp.content

    def _upload_media(self, file_path: str, media_type: str = 'image') -> str:
        """上传本地文件为企业微信临时素材，返回 media_id"""
        token = self._get_access_token()
        url = f'https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={token}&type={media_type}'
        filename = os.path.basename(file_path)
        with open(file_path, 'rb') as f:
            resp = requests.post(url, files={'media': (filename, f)}, timeout=30)
        result = resp.json()
        if result.get('errcode', 0) != 0:
            logger.error(f"上传媒体素材失败: {result}")
            return ''
        media_id = result.get('media_id', '')
        logger.debug(f"媒体素材上传成功 media_id={media_id}")
        return media_id

    # ------------------------------------------------------------------ #
    #  签名与解密
    # ------------------------------------------------------------------ #

    def _verify_signature(self, msg_sig: str, timestamp: str, nonce: str, encrypt: str) -> bool:
        """验证 SHA1(sort(token, timestamp, nonce, encrypt))"""
        parts = sorted([self.token, timestamp, nonce, encrypt])
        sha1 = hashlib.sha1(''.join(parts).encode('utf-8')).hexdigest()
        return sha1 == msg_sig

    def _decrypt(self, cipher_b64: str) -> str:
        """
        解密企业微信 AES-256-CBC 消息。
        格式（解密后去 PKCS#7 padding）：
          [16字节随机] [4字节消息长度大端] [消息内容] [corp_id]
        """
        cipher_data = base64.b64decode(cipher_b64)
        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        plain = cipher.decrypt(cipher_data)

        # PKCS#7 unpad
        pad = plain[-1]
        plain = plain[:-pad]

        # 解析结构
        msg_len = struct.unpack('>I', plain[16:20])[0]
        msg = plain[20:20 + msg_len].decode('utf-8')
        corp_id = plain[20 + msg_len:].decode('utf-8')

        if corp_id != self.corp_id:
            raise ValueError(f"corp_id 不匹配: expected={self.corp_id}, got={corp_id}")

        return msg

    # ------------------------------------------------------------------ #
    #  工具方法
    # ------------------------------------------------------------------ #

    def _is_duplicate(self, msg_id: str) -> bool:
        """基于 msg_id 的 60s 去重"""
        now = time.time()
        with self._dedup_lock:
            # 清理过期条目
            expired = [k for k, t in self._seen_msg_ids.items() if now - t > 60]
            for k in expired:
                del self._seen_msg_ids[k]

            if msg_id in self._seen_msg_ids:
                return True
            self._seen_msg_ids[msg_id] = now
            return False

    @staticmethod
    def _split_by_bytes(s: str, max_bytes: int) -> list[str]:
        """按 UTF-8 字节长度分片，避免截断多字节字符"""
        if len(s.encode('utf-8')) <= max_bytes:
            return [s]
        parts = []
        while s:
            encoded = s.encode('utf-8')
            if len(encoded) <= max_bytes:
                parts.append(s)
                break
            # 找安全切割点
            end = max_bytes
            while end > 0 and (encoded[end] & 0b11000000) == 0b10000000:
                end -= 1
            parts.append(encoded[:end].decode('utf-8'))
            s = encoded[end:].decode('utf-8')
        return parts
