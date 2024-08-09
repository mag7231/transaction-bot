# -*- coding: utf-8 -*-
import asyncio
import json
import requests
from websockets import connect, ConnectionClosed
import os
from dotenv import load_dotenv
import nest_asyncio
from web3 import Web3
from eth_abi import decode
from eth_utils import to_checksum_address

# Применение nest_asyncio для совместимости с уже работающими циклами событий
nest_asyncio.apply()

# Загрузка переменных из .env файла
load_dotenv()

# Токен вашего Telegram бота
telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
if not telegram_token:
    raise Exception("Telegram bot token is not set")

# ID чата, куда будут отправляться уведомления
chat_id = os.getenv("TELEGRAM_CHAT_ID")
if not chat_id:
    raise Exception("Telegram chat ID is not set")

# Проверка наличия INFURA_PROJECT_ID
infura_project_id = os.getenv("INFURA_PROJECT_ID")
if not infura_project_id:
    raise Exception("INFURA project ID is not set")

# Адрес бота Banana Gun
bot_address = "0x3328F7f4A1D1C57c35df56bBf0c9dCAFCA309C49"
print(f"Bot address: {bot_address}")

# Инициализация Web3
infura_url = f"https://mainnet.infura.io/v3/{infura_project_id}"
web3 = Web3(Web3.HTTPProvider(infura_url))

# Устанавливаем минимальный порог для суммы сделки
MINIMUM_ETH_AMOUNT = 0.01

# Адрес, от которого мы хотим отслеживать транзакции
TARGET_ADDRESS = "0x711481A95508Cf21d3f5F94d95aD8145076cbFff".lower()

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text
    }
    response = requests.post(url, data=data)
    return response.json()

async def ping_connection(ws):
    while True:
        await asyncio.sleep(30)  # Отправлять пинг каждые 30 секунд
        try:
            await ws.ping()
            print("Ping sent")
        except ConnectionClosed as e:
            print(f"Connection closed: {e}. Reconnecting...")
            break

async def get_event():
    while True:
        try:
            async with connect(f"wss://mainnet.infura.io/ws/v3/{infura_project_id}") as ws:
                await ws.send(json.dumps({"id": 1, "method": "eth_subscribe", "params": ["newHeads"]}))
                subscription_response = await ws.recv()
                print(subscription_response)
                subscription_id = json.loads(subscription_response)['result']
                print(f"Subscribed with ID: {subscription_id}")
                
                # Запуск задачи для отправки пингов
                ping_task = asyncio.create_task(ping_connection(ws))
                
                while True:
                    try:
                        message = await ws.recv()
                        block_hash = json.loads(message)['params']['result']['hash']
                        await handle_new_block(block_hash)
                    except ConnectionClosed as e:
                        print(f"Connection closed: {e}. Reconnecting...")
                        ping_task.cancel()  # Отмена задачи пинга при разрыве соединения
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass
                        break
                    except Exception as e:
                        print(f"Error: {e}")
                        continue
        except Exception as e:
            print(f"Error: {e}. Reconnecting...")
            await asyncio.sleep(5)  # Подождать 5 секунд перед повторным подключением

async def handle_new_block(block_hash):
    try:
        block = web3.eth.get_block(block_hash, True)
        for tx in block.transactions:
            # Проверяем, является ли отправитель целевым адресом
            if tx['from'].lower() == TARGET_ADDRESS:
                # Проверяем, что транзакция направлена на bot_address и что она больше MINIMUM_ETH_AMOUNT
                value_in_eth = web3.from_wei(tx['value'], 'ether')
                if tx['to'] and tx['to'].lower() == bot_address.lower() and value_in_eth >= MINIMUM_ETH_AMOUNT:
                    print(f"Incoming transaction detected to bot address: {tx['hash'].hex()} from {TARGET_ADDRESS}")
                    tx_receipt = web3.eth.get_transaction_receipt(tx['hash'])
                    logs = tx_receipt['logs']
                    for log in logs:
                        print(f"Log topics: {log['topics']}")
                        print(f"Log data: {log['data']}")
                        
                        topics = log['topics']
                        data = log['data']
                        token_address = None
                        
                        # Если это событие Transfer, мы можем извлечь адрес токена из контракта
                        if topics[0].hex() == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef':
                            try:
                                token_address = log['address']  # Адрес контракта, генерирующего событие
                                print(f"Token address (from contract): {token_address}")
                            except Exception as e:
                                print(f"Unable to decode token address from log: {e}")
                        
                        # Если это не событие Transfer, проверяем другие варианты
                        if not token_address and len(topics) >= 3:
                            try:
                                token_address = to_checksum_address(topics[2].hex()[26:])
                                print(f"Token address (from topic[2]): {token_address}")
                            except Exception as e:
                                print(f"Unable to decode token address from topics: {e}")
                        
                        if token_address:
                            # Декодируем сумму из данных, если возможно
                            try:
                                if len(data) >= 66:  # Данные должны быть 32 байта для суммы
                                    amount_wei = int(data, 16)
                                    amount_eth = web3.from_wei(amount_wei, 'ether')
                                    print(f"Amount found: {amount_eth} ETH")
                                    
                                    # Проверка, превышает ли сумма минимальный порог
                                    if amount_eth >= MINIMUM_ETH_AMOUNT:
                                        message = (f"Transaction detected from {TARGET_ADDRESS}:\n"
                                                   f"Token address - {token_address}\n"
                                                   f"Amount - {amount_eth} ETH")
                                        send_telegram_message(message)
                                    else:
                                        print(f"Transaction amount {amount_eth} ETH is below threshold.")
                                else:
                                    print(f"Log data length is insufficient for decoding amount.")
                            except Exception as e:
                                print(f"Unable to decode amount: {e}")
                                continue
                        else:
                            print("Token address could not be determined")
    except Exception as e:
        print(f"Error in handle_new_block: {e}")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(get_event())
