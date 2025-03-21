# alert_handler.py
import os
import logging
import sys
import traceback
from datetime import datetime
from flask import Flask, request, jsonify
import requests
import json
from openai import OpenAI

# 配置结构化日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def validate_envs():
    """环境变量验证（带详细日志）"""
    logger.info("开始环境变量验证")

    required_vars = {
        'DEEPSEEK_API_KEY': None,
        'WEBHOOK_URL': None,
        'NOTIFY_CHANNEL': None,
    }

    try:
        notify_channel = os.getenv('NOTIFY_CHANNEL', 'feishu')
        logger.info(f"当前通知渠道配置: {notify_channel}")

        if notify_channel not in ['feishu', 'dingtalk']:
            logger.error(f"非法的通知渠道配置: {notify_channel}")
            raise ValueError("非法通知渠道")

        missing = []
        for var, channels in required_vars.items():
            current_value = os.getenv(var)
            logger.debug(f"检查环境变量 {var} => {'已设置' if current_value else '未设置'}")

            # 需要验证的条件
            if not current_value and (channels is None or notify_channel in channels):
                missing.append(var)

        if missing:
            logger.error(f"缺失关键环境变量: {missing}")
            raise EnvironmentError(f"缺少环境变量: {', '.join(missing)}")

        logger.info("环境变量验证通过")

    except Exception as e:
        logger.critical("环境变量验证失败，服务终止")
        raise

def convert_to_local(utc_time_str):
    """时间转换（带错误日志）"""
    logger.debug(f"开始时间转换，原始时间: {utc_time_str}")

    try:
        # 清理时间字符串
        clean_time = utc_time_str.rstrip('Z')
        logger.debug(f"清理后时间字符串: {clean_time}")

        utc_time = datetime.fromisoformat(clean_time)
        logger.debug(f"解析为UTC时间: {utc_time}")

        local_time = utc_time.astimezone()
        formatted_time = local_time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"时间转换完成: {utc_time_str} => {formatted_time}")

        return formatted_time
    except Exception as e:
        logger.error(f"时间转换失败: {str(e)}")
        logger.debug(f"错误详情: {traceback.format_exc()}")
        return "时间解析错误"

def load_alert_templates():
    """加载告警模板"""
    logger.info("加载告警模板")

    try:
        templates_path = '/app/templates/alert-templates.json'
        logger.debug(f"模板文件路径: {templates_path}")

        with open(templates_path, 'r', encoding='utf-8') as f:
            templates = json.load(f)
            logger.info("告警模板加载成功")
            logger.debug(f"加载的模板内容: {json.dumps(templates, ensure_ascii=False)}")
            return templates

    except Exception as e:
        logger.error(f"加载告警模板失败: {str(e)}")
        logger.debug(f"错误详情: {traceback.format_exc()}")
        raise

def get_template_data(alert):
    """从告警数据中提取模板需要的数据"""
    logger.info("从告警数据中提取模板需要的数据")

    try:
        # 提取基础信息
        labels = alert.get('labels', {})
        annotations = alert.get('annotations', {})
        status = alert.get('status', 'unknown').upper()

        # 时间处理
        starts_at = convert_to_local(alert.get('startsAt', ''))
        ends_at = "告警持续中，尚未恢复" if alert.get('endsAt') == "0001-01-01T00:00:00Z" \
            else convert_to_local(alert.get('endsAt', ''))

        # 构造数据字典
        data = {
            'alertname': labels.get('alertname', '告警'),
            'severity': labels.get('severity', '级别'),
            'level': labels.get('level', '等级'),
            'instance': labels.get('instance','事例'),
            'monitor': labels.get('monitor','环境'),
            'starts_at': starts_at,
            'fingerprint': alert.get('fingerprint','指纹'),
            'ends_at': ends_at,
            'description': annotations.get('description', '无描述信息'),
            'summary': annotations.get('summary', '无描述信息'),
            'status': status,
            'labels': labels,
            'annotations': annotations
        }

        logger.debug(f"提取的数据: {json.dumps(data, ensure_ascii=False)}")
        return data

    except Exception as e:
        logger.error(f"提取模板数据失败: {str(e)}")
        logger.debug(f"错误详情: {traceback.format_exc()}")
        raise

def build_alert_content(alerts):
    """告警内容构建（带处理日志）"""
    logger.info(f"开始构建告警内容，共 {len(alerts)} 条告警")

    # 加载告警模板
    templates = load_alert_templates()

    content = []
    for idx, alert in enumerate(alerts, 1):
        logger.info(f"正在处理第 {idx}/{len(alerts)} 条告警")

        # 提取模板数据
        template_data = get_template_data(alert)

        # 根据状态选择模板
        if alert.get('status') == 'resolved':
            logger.info(f"告警 {idx} 为✅ 告警恢复")
            template = templates.get('resolved_template')
        else:
            logger.info(f"告警 {idx} 为触发通知")
            template = templates.get('firing_template')

        # 填充模板
        try:
            section = []
            for part in template:
                # 动态替换占位符
                filled_part = part.format(**template_data)
                section.append(filled_part)
        except KeyError as e:
            logger.error(f"模板占位符 {str(e)} 未找到")
            section = [f"模板占位符错误: {str(e)}"]

        content.extend(section)
        logger.debug(f"告警 {idx} 生成内容:\n{''.join(section)}")

    logger.info("告警内容构建完成")
    return "\n".join(content)

def call_deepseek(alert):
    """AI分析调用（带详细日志）"""
    logger.info("开始调用DeepSeek API")

    try:
        api_key = os.getenv('DEEPSEEK_API_KEY')
        logger.debug(f"使用API密钥前4位: {api_key[:4]}****")

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com"
        )

        request_content = f"分析Prometheus告警（精简）:{json.dumps(alert, indent=2)}"
        logger.debug(f"API请求内容:\n{request_content}")

        logger.info("正在发送API请求...")
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": request_content}]
        )

        logger.info("DeepSeek API调用成功")
        logger.debug(f"API响应完整数据: {response}")

        if response.choices and len(response.choices) > 0:
            result = response.choices[0].message.content
            logger.info(f"获取到分析建议（长度:{len(result)}字符）")
            logger.debug(f"分析建议内容:\n{result}")
            return result

        logger.error("API返回空结果")
        return None

    except Exception as e:
        logger.error(f"DeepSeek调用失败: {str(e)}")
        logger.debug(f"错误堆栈:\n{traceback.format_exc()}")
        return None

def send_feishu_message(alerts, analysis=None):
    """飞书消息发送（带详细日志）"""
    logger.info("开始发送飞书消息")

    try:
        webhook_url = os.getenv('WEBHOOK_URL')
        logger.debug(f"飞书Webhook URL: {webhook_url[:30]}...")  # 避免打印完整URL

        # 内容构建
        logger.info("构建消息内容")
        alert_content = build_alert_content(alerts)
        is_resolved = any(a.get('status') == 'resolved' for a in alerts)
        logger.info(f"消息类型: {'✅ 告警恢复' if is_resolved else '🚨 告警通知'}")

        # 构造消息元素
        elements = [ {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": alert_content
            }
        } ]
        logger.debug("基础内容构建完成")

        # 添加分析建议
        if analysis and not is_resolved:
            logger.info("添加智能分析建议")
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**🛠️ AI处理建议如下================**\n{analysis}"
                }
            })

        # 构造完整载荷
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": "✅ 告警恢复" if is_resolved else "🚨 告警通知"
                    },
                    "template": "green" if is_resolved else "red"
                },
                "elements": elements
            }
        }
        logger.debug(f"完整请求载荷:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")

        # 发送请求
        logger.info("正在发送飞书消息...")
        start_time = datetime.now()
        resp = requests.post(webhook_url, json=payload)
        latency = (datetime.now() - start_time).total_seconds() * 1000
        logger.info(f"收到飞书响应，状态码: {resp.status_code} 耗时: {latency:.2f}ms")

        # 处理响应
        resp_data = resp.json()
        logger.debug(f"飞书完整响应: {json.dumps(resp_data, ensure_ascii=False)}")

        if resp_data.get('StatusCode') == 0:
            logger.info("飞书消息发送成功")
            return True

        logger.error(f"飞书消息发送失败: {resp_data.get('msg')}")
        return False

    except Exception as e:
        logger.error(f"飞书消息发送异常: {str(e)}")
        logger.debug(f"错误堆栈:\n{traceback.format_exc()}")
        return False

def send_dingtalk_message(alerts, analysis=None):
    """钉钉消息发送（带详细日志）"""
    logger.info("开始发送钉钉消息")

    try:
        webhook_url = os.getenv('WEBHOOK_URL')
        logger.debug(f"钉钉Webhook URL: {webhook_url[:30]}...")  # 避免打印完整URL

        # 内容构建
        logger.info("构建消息内容")
        alert_content = build_alert_content(alerts)
        is_resolved = any(a.get('status') == 'resolved' for a in alerts)
        logger.info(f"消息类型: {'✅ 告警恢复' if is_resolved else '🚨 告警通知'}")

        # 构造文本内容
        title = "✅ 告警恢复" if is_resolved else "🚨 告警通知"
        text = f"### {title}\n{alert_content}"

        if analysis and not is_resolved:
            logger.info("添加智能分析建议")
            text += f"\n\n**🛠️ AI处理建议如下================****\n{analysis}"

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text
            }
        }
        logger.debug(f"完整请求载荷:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")

        # 发送请求
        logger.info("正在发送钉钉消息...")
        start_time = datetime.now()
        resp = requests.post(webhook_url, json=payload)
        latency = (datetime.now() - start_time).total_seconds() * 1000
        logger.info(f"收到钉钉响应，状态码: {resp.status_code} 耗时: {latency:.2f}ms")

        # 处理响应
        resp_data = resp.json()
        logger.debug(f"钉钉完整响应: {json.dumps(resp_data, ensure_ascii=False)}")

        if resp_data.get('errcode') == 0:
            logger.info("钉钉消息发送成功")
            return True

        logger.error(f"钉钉消息发送失败: {resp_data.get('errmsg')}")
        return False

    except Exception as e:
        logger.error(f"钉钉消息发送异常: {str(e)}")
        logger.debug(f"错误堆栈:\n{traceback.format_exc()}")
        return False

@app.route('/handle_alert', methods=['POST'])
def handle_alert():
    """请求处理入口（带全链路日志）"""
    logger.info("="*50)
    logger.info("收到新的告警请求")

    try:
        # 记录请求基本信息
        client_ip = request.remote_addr
        logger.info(f"请求来源IP: {client_ip}")
        logger.debug(f"请求头信息: {dict(request.headers)}")

        # 解析请求体
        try:
            data = request.get_json()
            logger.info("成功解析JSON数据")
            logger.debug(f"原始请求数据:\n{json.dumps(data, indent=2, ensure_ascii=False)}")
        except Exception as e:
            logger.error("JSON解析失败")
            logger.debug(f"错误内容: {request.data}")
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        alerts = data.get('alerts', [])
        logger.info(f"获取到 {len(alerts)} 条告警信息")

        if not alerts:
            logger.warning("收到空告警列表，跳过处理")
            return jsonify({"status": "ignored"})

        # 判断告警状态
        is_resolved = all(a.get('status') == 'resolved' for a in alerts)
        logger.info(f"告警状态: {'全部已恢复' if is_resolved else '包含未恢复告警'}")

        # AI分析
        analysis = None
        if not is_resolved:
            logger.info("启动DeepSeek分析流程")
            analysis = call_deepseek(data)
            if analysis:
                logger.info("成功获取分析建议")
            else:
                logger.warning("未能获取分析建议")
        else:
            logger.info("跳过分析建议（✅ 告警恢复）")

        # 发送通知
        channel = os.getenv('NOTIFY_CHANNEL', 'feishu')
        logger.info(f"选择通知渠道: {channel}")

        success = False
        if channel == 'feishu':
            success = send_feishu_message(alerts, analysis)
        elif channel == 'dingtalk':
            success = send_dingtalk_message(alerts, analysis)

        logger.info(f"最终处理结果: {'成功' if success else '失败'}")
        return jsonify({"status": "success" if success else "failed"})

    except Exception as e:
        logger.critical("处理流程异常中断")
        logger.error(f"未捕获的异常: {str(e)}")
        logger.debug(f"完整堆栈跟踪:\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 500

    finally:
        logger.info("请求处理完成")
        logger.info("="*50)

if __name__ == '__main__':
    try:
        logger.info("="*50)
        logger.info("启动告警处理服务")
        validate_envs()
        logger.info(f"服务监听端口: 5000")
        app.run(host='0.0.0.0', port=5000)
    except Exception as e:
        logger.critical("服务启动失败")
        sys.exit(1)