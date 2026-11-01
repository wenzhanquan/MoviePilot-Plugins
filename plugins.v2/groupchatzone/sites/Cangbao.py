from typing import Tuple, Optional, Dict
from lxml import etree
import time
import re

from app.log import logger
from .NexusPHP import NexusPHPHandler
from . import ISiteHandler

class CangBaoHandler(NexusPHPHandler):
    """
    藏宝阁站点处理类
    """
    
    def match(self) -> bool:
        """
        判断是否为藏宝阁站点
        """
        return "藏宝阁" in self.site_name

    def send_messagebox(self, message: str = None, callback=None) -> Tuple[bool, str]:
        """
        发送群聊区消息
        :param message: 消息内容
        :param callback: 回调函数
        :return: 发送结果
        """
        try:
            result = ISiteHandler.send_messagebox(self, message, callback)
            if not result[0]:
                return result

            # 获取当前用户名
            username = self.get_username()
            if not username:
                self._last_message_result = result[1]
                return result

            feedback = self._poll_feedback(username, message)
            if feedback:
                logger.info(f"获取到反馈消息: {feedback}")
                result = (True, feedback)
            else:
                logger.info("未获取到相关反馈消息，保留发送结果")
                
            self._last_message_result = result[1] if result[0] else None
            return result
            
        except Exception as e:
            logger.error(f"获取反馈消息失败: {str(e)}")
            return result

    def _poll_feedback(self, username: str, message: str = None) -> Optional[str]:
        """
        轮询藏宝阁 shoutbox，获取系统反馈
        """
        reward_keyword = None
        if message:
            if "上传" in message:
                reward_keyword = "上传量"
            elif "魔力" in message:
                reward_keyword = "魔力值"

        for attempt in range(5):
            if attempt > 0:
                time.sleep(3)

            response = self._send_get_request(self.shoutbox_url)
            if not response:
                continue

            html = etree.HTML(response.text)
            if html is None:
                continue

            rows = html.xpath("//td[contains(@class, 'shoutrow')][position() <= 20]")
            for row in rows:
                row_content = self._extract_row_text(row)
                feedback = self._match_feedback(row_content, username, reward_keyword)
                if feedback:
                    return feedback

        return None

    def _extract_row_text(self, row) -> str:
        """
        提取并清理单条 shoutbox 文本
        """
        row_content = "".join(row.xpath(".//text()[not(ancestor::span[@class='date'])]")).strip()
        return re.sub(r"\s+", " ", row_content)

    def _match_feedback(self, row_content: str, username: str, reward_keyword: Optional[str] = None) -> Optional[str]:
        """
        匹配藏宝阁系统反馈消息
        """
        if not row_content.startswith("系统:"):
            return None

        if f"感谢 @{username} 的支持" in row_content:
            if reward_keyword and reward_keyword not in row_content:
                return None
            return row_content

        if f"@{username} 您今天已经求过奖励啦" in row_content:
            return row_content

        return None

    def get_feedback(self, message: str = None) -> Optional[Dict]:
        """
        获取消息反馈
        """
        return super().get_feedback(message)
