"""
表情包收集服务
负责智能收集和管理用户发送的表情包
"""
import os
import json
import logging
import shutil
from datetime import datetime
from typing import Optional, Dict
from src.services.ai.llm_service import LLMService

logger = logging.getLogger('main')


class StickerCollector:
    """表情包智能收集器"""

    def __init__(self, root_dir: str, llm_service: LLMService, avatar_name: str):
        self.root_dir = root_dir
        self.llm_service = llm_service
        self.avatar_name = avatar_name

        # 表情包存储目录
        self.sticker_base_dir = os.path.join(
            root_dir, "data", "avatars", avatar_name, "emojis"
        )

        # 收集记录文件
        self.collection_log = os.path.join(
            root_dir, "data", "avatars", avatar_name, "sticker_collection.json"
        )

        os.makedirs(self.sticker_base_dir, exist_ok=True)

    def should_collect(self, image_path: str, context: str = "") -> Optional[Dict]:
        """判断是否应该收集这个表情包"""
        try:
            prompt = f"""分析这张图片是否适合作为表情包收藏。

判断标准：
1. 是否是表情包（而非普通照片、截图等）
2. 表达的情感是否清晰
3. 是否有收藏价值

如果适合收藏，返回JSON格式：
{{"collect": true, "emotion": "情感类型（如happy/sad/angry等）", "description": "简短描述"}}

如果不适合，返回：
{{"collect": false, "reason": "原因"}}

对话上下文：{context if context else "无"}"""

            # 调用视觉模型分析
            response = self.llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )

            # 解析响应
            result = json.loads(response.strip())
            logger.info(f"表情包分析结果: {result}")
            return result

        except Exception as e:
            logger.error(f"分析表情包失败: {e}")
            return None

    def collect_sticker(self, image_path: str, emotion: str, description: str,
                       user_id: str) -> bool:
        """收集表情包到对应情感目录"""
        try:
            # 创建情感目录
            emotion_dir = os.path.join(self.sticker_base_dir, emotion.lower())
            os.makedirs(emotion_dir, exist_ok=True)

            # 生成文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = os.path.splitext(image_path)[1]
            new_filename = f"user_{timestamp}{ext}"
            target_path = os.path.join(emotion_dir, new_filename)

            # 复制文件
            shutil.copy2(image_path, target_path)

            # 记录收集信息
            self._log_collection(user_id, emotion, description, target_path)

            logger.info(f"已收集表情包: {emotion} -> {target_path}")
            return True

        except Exception as e:
            logger.error(f"收集表情包失败: {e}")
            return False

    def _log_collection(self, user_id: str, emotion: str, description: str,
                       file_path: str):
        """记录收集日志"""
        try:
            # 读取现有日志
            if os.path.exists(self.collection_log):
                with open(self.collection_log, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
            else:
                logs = []

            # 添加新记录
            logs.append({
                "timestamp": datetime.now().isoformat(),
                "user_id": user_id,
                "emotion": emotion,
                "description": description,
                "file_path": file_path
            })

            # 保存日志
            with open(self.collection_log, 'w', encoding='utf-8') as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"记录收集日志失败: {e}")
