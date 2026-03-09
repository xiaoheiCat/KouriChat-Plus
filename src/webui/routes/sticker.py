"""
表情包管理路由
"""
import os
import json
import logging
from flask import Blueprint, render_template, jsonify, request, send_file
from datetime import datetime

logger = logging.getLogger('main')

sticker_bp = Blueprint('sticker', __name__, url_prefix='/sticker')


def get_root_dir():
    """获取项目根目录"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@sticker_bp.route('/manage')
def manage():
    """表情包管理页面"""
    # 导入配置解析函数
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    from run_config_web import parse_config_groups

    config_groups = parse_config_groups()
    return render_template('sticker_manage.html', config_groups=config_groups)


@sticker_bp.route('/list')
def list_stickers():
    """获取表情包列表"""
    try:
        from data.config import config
        root_dir = get_root_dir()
        avatar_name = os.path.basename(config.behavior.context.avatar_dir)
        sticker_dir = os.path.join(root_dir, "data", "avatars", avatar_name, "emojis")

        stickers = {}
        if os.path.exists(sticker_dir):
            for emotion in os.listdir(sticker_dir):
                emotion_path = os.path.join(sticker_dir, emotion)
                if os.path.isdir(emotion_path):
                    files = [f for f in os.listdir(emotion_path)
                            if f.lower().endswith(('.jpg', '.png', '.gif', '.jpeg'))]
                    stickers[emotion] = {
                        'count': len(files),
                        'files': files
                    }

        return jsonify({'success': True, 'stickers': stickers})
    except Exception as e:
        logger.error(f"获取表情包列表失败: {e}")
        return jsonify({'success': False, 'error': str(e)})


@sticker_bp.route('/logs')
def get_logs():
    """获取收集日志"""
    try:
        from data.config import config
        root_dir = get_root_dir()
        avatar_name = os.path.basename(config.behavior.context.avatar_dir)
        log_file = os.path.join(root_dir, "data", "avatars", avatar_name, "sticker_collection.json")

        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8') as f:
                logs = json.load(f)
            return jsonify({'success': True, 'logs': logs[-50:]})  # 最近50条
        return jsonify({'success': True, 'logs': []})
    except Exception as e:
        logger.error(f"获取收集日志失败: {e}")
        return jsonify({'success': False, 'error': str(e)})


@sticker_bp.route('/delete', methods=['POST'])
def delete_sticker():
    """删除表情包"""
    try:
        data = request.json
        emotion = data.get('emotion')
        filename = data.get('filename')

        from data.config import config
        root_dir = get_root_dir()
        avatar_name = os.path.basename(config.behavior.context.avatar_dir)
        file_path = os.path.join(root_dir, "data", "avatars", avatar_name, "emojis", emotion, filename)

        if os.path.exists(file_path):
            os.remove(file_path)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': '文件不存在'})
    except Exception as e:
        logger.error(f"删除表情包失败: {e}")
        return jsonify({'success': False, 'error': str(e)})


@sticker_bp.route('/image/<emotion>/<filename>')
def get_image(emotion, filename):
    """获取表情包图片"""
    try:
        from data.config import config
        root_dir = get_root_dir()
        avatar_name = os.path.basename(config.behavior.context.avatar_dir)
        image_dir = os.path.join(root_dir, "data", "avatars", avatar_name, "emojis", emotion)
        return send_file(os.path.join(image_dir, filename))
    except Exception as e:
        logger.error(f"获取图片失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 404
