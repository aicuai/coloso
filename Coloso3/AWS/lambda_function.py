import json
import os
import base64
import boto3
import logging
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageSendMessage
)
import requests
import uuid

# ロガーの設定
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 環境変数
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
COMFYUI_LAMBDA_ARN = os.getenv('COMFYUI_LAMBDA_ARN')
BUCKET_NAME = os.getenv('IMAGE_BUCKET')

# LINEクライアントの初期化
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# Lambda clientの初期化
lambda_client = boto3.client('lambda')

def upload_to_s3(image_data: bytes) -> str:
    """画像データをS3にアップロードし、URLを取得"""
    s3_client = boto3.client('s3')
    file_name = f"generated/{str(uuid.uuid4())}.jpg"
    
    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=file_name,
        Body=image_data,
        ContentType='image/jpeg'
    )
    
    url = s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': BUCKET_NAME, 'Key': file_name},
        ExpiresIn=3600
    )
    return url

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    """テキストメッセージの処理"""
    user_id = event.source.user_id
    prompt = event.message.text
    
    try:
        # 処理中メッセージを送信
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text="画像を生成中です...")
        )
        
        # ComfyUI Lambda関数を呼び出し
        payload = {
            "body": json.dumps({
                "positive_prompt": prompt,
                "negative_prompt": "worst quality, low quality, bad anatomy, jpeg artifacts, nsfw, nude, naked,",
                "prompt_file": "workflow_api.json"
            })
        }
        
        logger.info("Invoking ComfyUI Lambda with payload: %s", payload)
        
        response = lambda_client.invoke(
            FunctionName=COMFYUI_LAMBDA_ARN,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )
        
        # レスポンスの処理
        response_payload = json.loads(response['Payload'].read())
        logger.info("ComfyUI Lambda response: %s", response_payload)
        
        image_data = base64.b64decode(response_payload['body'])
        
        # 画像をS3にアップロード
        image_url = upload_to_s3(image_data)
        logger.info("Image uploaded to S3: %s", image_url)
        
        # 生成された画像を送信
        line_bot_api.push_message(
            user_id,
            ImageSendMessage(
                original_content_url=image_url,
                preview_image_url=image_url
            )
        )
        
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text="申し訳ありません。画像の生成中にエラーが発生しました。")
        )

def lambda_handler(event, context):
    logger.info("Full event: %s", json.dumps(event))
    
    # プロキシ統合のためのヘッダー処理
    if 'headers' not in event:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'No headers in event'})
        }
    
    # X-Line-Signatureヘッダーの取得（大文字小文字両方に対応）
    headers = {k.lower(): v for k, v in event['headers'].items()}
    signature = headers.get('x-line-signature')
    
    if not signature:
        logger.error("No Line signature found in headers")
        logger.error("Available headers: %s", headers)
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'No Line signature'})
        }
    
    # リクエストボディの取得とデコード
    body = event.get('body', '')
    is_base64_encoded = event.get('isBase64Encoded', False)
    
    if is_base64_encoded:
        body = base64.b64decode(body).decode('utf-8')
    
    if not body:
        logger.error("No body found in event")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'No body'})
        }
    
    try:
        # デバッグ用にボディの内容をログ出力
        logger.info("Decoded body: %s", body)
        
        # LINE Webhookの処理
        # handler.handle()だけを使用し、手動のイベント処理は削除
        handler.handle(body, signature)
        
    except InvalidSignatureError:
        logger.error("Invalid Line signature")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid signature'})
        }
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
    
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'OK'})
    }