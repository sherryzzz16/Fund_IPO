#!/usr/bin/env python3
import urllib.request
import urllib.error
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

# ================= 配置区域 Configuration =================

# 你的企业微信 Webhook URL。
WECOM_WEBHOOK_URL = os.environ.get("WECOM_WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=PLACEHOLDER")

# 记录已通知过的 IPO 的状态文件
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_ipos.json")

# SEC 要求携带邮箱地址
SEC_USER_AGENT = "Antigravity-Monitor test@example.com"

# 过滤关键字，只有公司名称包含这些关键字（忽略大小写）时才提醒
TARGET_KEYWORDS = [
    "fund", 
    "venture", 
    "innovation", 
    "capital", 
    "acquisition",       # 这里包含很多 SPAC
    "holdings", 
    "trust", 
    "investment",
    "group"
]

# ========================================================

def load_seen_ipos():
    """读取已通知的 IPO 列表"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"读取状态文件失败: {e}", file=sys.stderr)
            return []
    return []

def save_seen_ipos(seen_list):
    """保存已通知的 IPO 列表"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(seen_list, f, indent=4)
    except Exception as e:
        print(f"写入状态文件失败: {e}", file=sys.stderr)

def matches_keywords(company_name):
    """检查公司名是否匹配特定的基金、投资类关键字"""
    if not company_name:
        return False
    name_lower = company_name.lower()
    for kw in TARGET_KEYWORDS:
        if kw in name_lower:
            return True
    return False

def fetch_sec_edgar_rss(form_type):
    """从 SEC EDGAR 拉取最新的特定表单记录"""
    url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form_type}&output=atom"
    req = urllib.request.Request(url, headers={'User-Agent': SEC_USER_AGENT})
    results = []
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read()
            root = ET.fromstring(html)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('atom:entry', ns)
            
            for entry in entries:
                title_elem = entry.find('atom:title', ns)
                link_elem = entry.find('atom:link', ns)
                id_elem = entry.find('atom:id', ns)
                updated_elem = entry.find('atom:updated', ns)
                
                title = title_elem.text if title_elem is not None else ""
                
                # 提取公司名称: 如 "S-1/A - VIDA Global Inc. (0001973062) (Filer)" -> "VIDA Global Inc."
                company_name = title
                if " - " in title:
                    parts = title.split(" - ", 1)
                    if len(parts) > 1:
                        company_name = parts[1]
                        if " (" in company_name:
                            company_name = company_name.split(" (")[0].strip()

                link = link_elem.attrib['href'] if link_elem is not None else ""
                entry_id = id_elem.text if id_elem is not None else link
                updated = updated_elem.text if updated_elem is not None else ""
                
                results.append({
                    "id": entry_id,
                    "companyName": company_name,
                    "formType": form_type,
                    "link": link,
                    "updated": updated,
                    "rawTitle": title
                })
    except Exception as e:
        print(f"[{datetime.now()}] 抓取 SEC {form_type} 失败: {e}", file=sys.stderr)
        
    return results

def fetch_nasdaq_ipo_calendar():
    """获取纳斯达克当月 IPO 日历数据"""
    current_month_str = datetime.now().strftime("%Y-%m")
    url = f"https://api.nasdaq.com/api/ipo/calendar?date={current_month_str}"
    
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    })
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data.get("data", {})
    except Exception as e:
        print(f"[{datetime.now()}] 抓取 Nasdaq IPO 数据失败: {e}", file=sys.stderr)
        return None

def send_wecom_notification(message):
    """发送企业微信消息"""
    if "PLACEHOLDER" in WECOM_WEBHOOK_URL:
        print(f"[{datetime.now()}] Webhook URL 是占位符，跳过发送消息。消息内容:\n{message}")
        return False
        
    data = {"msgtype": "text", "text": {"content": message}}
    req = urllib.request.Request(WECOM_WEBHOOK_URL, data=json.dumps(data).encode('utf-8'), method="POST")
    req.add_header('Content-Type', 'application/json')
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('errcode') == 0:
                print(f"[{datetime.now()}] 企业微信通知发送成功。")
                return True
            else:
                print(f"[{datetime.now()}] 发送失败: {result}", file=sys.stderr)
                return False
    except Exception as e:
        print(f"[{datetime.now()}] 请求企业微信失败: {e}", file=sys.stderr)
        return False

def format_sec_message(deal):
    """格式化 SEC 来源的单条记录"""
    message = f"【SEC 申报预警：{deal['formType']}】\n"
    message += f"👔 公司名称：{deal['companyName']}\n"
    message += f"📝 源标题：{deal['rawTitle']}\n"
    message += f"📅 披露时间：{deal['updated']}\n"
    message += f"🔗 详情链接：{deal['link']}\n"
    message += "\n"
    return message

def format_deal_message(deal, context_type):
    """格式化纳斯达克 IPO 记录"""
    company_name = deal.get("companyName", "N/A")
    symbol = deal.get("proposedTickerSymbol", "N/A")
    amount = deal.get("dollarValueOfSharesOffered", "N/A")
    
    message = f"【IPO 即将上市：{context_type}】\n"
    message += f"👔 公司名称：{company_name}\n"
    message += f"📊 拟用代码：{symbol}\n"
    message += f"💰 拟募资金额：{amount}\n"
    
    shares = deal.get("sharesOffered", "N/A")
    price = deal.get("proposedSharePrice", "N/A")
    date = deal.get("expectedPriceDate", "N/A")
    exchange = deal.get("proposedExchange", "N/A")
    message += f"📈 发行股数：{shares}\n"
    message += f"💲 发行价格区间：{price}\n"
    message += f"📅 预计定价日：{date}\n"
    message += f"🏛 拟上市交易所：{exchange}\n"
    
    message += "\n"
    return message

def main():
    seen_ipos = load_seen_ipos()
    messages_to_send = []
    
    # 使用 set 可以更方便地排重，不过原本是 list
    current_seen_set = set(seen_ipos)
    new_seen_ipos = list(seen_ipos)

    # 1. 检查 SEC EDGAR (N-2 封闭式基金, S-1 国内公司, F-1 国际公司)
    sec_forms = ["N-2", "S-1", "F-1"]
    for form in sec_forms:
        sec_deals = fetch_sec_edgar_rss(form)
        for deal in sec_deals:
            deal_id = f"SEC_{deal['id']}"
            company_name = deal.get("companyName", "")
            
            if deal_id not in current_seen_set and matches_keywords(company_name):
                messages_to_send.append(format_sec_message(deal))
                current_seen_set.add(deal_id)
                new_seen_ipos.append(deal_id)

    # 2. 检查 Nasdaq (仅排查 Upcoming，因为 SEC 足够包含最新的 Filed)
    nasdaq_data = fetch_nasdaq_ipo_calendar()
    if nasdaq_data:
        upcoming_data = nasdaq_data.get("upcoming", {})
        if upcoming_data and upcoming_data.get("upcomingTable", {}).get("rows"):
            for deal in upcoming_data["upcomingTable"]["rows"]:
                deal_id = f"NASDAQ_{deal.get('dealID')}"
                company_name = deal.get("companyName", "")
                
                if deal_id not in current_seen_set and matches_keywords(company_name):
                    messages_to_send.append(format_deal_message(deal, "NASDAQ/NYSE"))
                    current_seen_set.add(deal_id)
                    new_seen_ipos.append(deal_id)

    if messages_to_send:
        header = "💡 专注基金/投资类公司的 IPO 监控雷达\n\n"
        final_message = header + "--------------------\n".join(messages_to_send)
        if send_wecom_notification(final_message):
            save_seen_ipos(new_seen_ipos)
    else:
        print(f"[{datetime.now()}] 本次运行未发现新的符合条件的 IPO")
        save_seen_ipos(new_seen_ipos)

if __name__ == "__main__":
    main()
