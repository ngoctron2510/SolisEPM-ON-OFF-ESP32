# ===================== BƯỚC 1: IMPORT HỆ THỐNG & CHỜ WiFi ỔN ĐỊNH =====================
import network
import time
import gc
import asyncio
import bluetooth
import aioble
import sys
import json
import struct
from machine import Pin, UART, WDT

gc.collect()

# ===================== BƯỚC 2: KHỞI TẠO BLE + HARDWARE =====================
_NUS_SERVICE_UUID = bluetooth.UUID("6e400001-b5a3-f393-e0a9-e50e24dcca9e")
_RX_CHAR_UUID = bluetooth.UUID("6e400002-b5a3-f393-e0a9-e50e24dcca9e")
_TX_CHAR_UUID = bluetooth.UUID("6e400003-b5a3-f393-e0a9-e50e24dcca9e")
ble_service = aioble.Service(_NUS_SERVICE_UUID)
rx_characteristic = aioble.Characteristic(
    ble_service, _RX_CHAR_UUID, write=True, capture=True, initial=b'\x00'*256
)
tx_characteristic = aioble.Characteristic(
    ble_service, _TX_CHAR_UUID, read=True, notify=True, initial=b'\x00'*256
)
try:
    aioble.register_services(ble_service)
    print('BLE services registered')
except Exception as e:
    print(f'Lỗi register BLE services: {e}')
    ble_service = None
gc.collect()

# Khởi tạo phần cứng
uart = UART(1, baudrate=9600, bits=8, parity=None, stop=1, tx=17, rx=16)
led = Pin(2, Pin.OUT)
led1 = Pin(15, Pin.OUT)
led2 = Pin(4, Pin.OUT)
WDT_TIMEOUT_MS = 10000
wdt = WDT(timeout=WDT_TIMEOUT_MS)
print(f'Watchdog Timer khởi tạo: {WDT_TIMEOUT_MS//1000} giây')
gc.collect()

# ===================== CẤU HÌNH MẶC ĐỊNH =====================
DEFAULT_CONFIG = {
    "slave_id": 101,
    "reg_addr": 36508,
    "value_on": 4400,
    "value_off": 0,
    "btn_pin": 5,
    "btn_mode": "PULL_UP",
    "btn_type": "momentary",
    "health_reg": 36017,
    "last_value": 0
}
CONFIG_FILE = 'modbus_config.json'
WIFI_CONFIG_FILE = 'wifi_config.json'

# ===================== CẤU HÌNH WiFi STA (Client) =====================
DEFAULT_WIFI_CONFIG = {
    "sta_enabled": True,
    "ssid": "TRONHN-TN-1",
    "password": "19001006"
}

def load_wifi_config():
    try:
        with open(WIFI_CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
            for k, v in DEFAULT_WIFI_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    except:
        return dict(DEFAULT_WIFI_CONFIG)

def save_wifi_config(cfg):
    with open(WIFI_CONFIG_FILE, 'w') as f:
        json.dump(cfg, f)

wifi_config = load_wifi_config()

# Các chân đã dùng: LED (2,4,15), UART (17 TX, 16 RX)
USED_PINS = [2, 4, 15, 17, 16]

# Toàn bộ GPIO có trên header ESP32 Dev Kit v1 (38-pin)
ESP32_PINS = [0, 1, 2, 3, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33, 34, 35, 36, 39]
# Trong đó: 34,35,36,39 là INPUT-ONLY (không dùng PULL_DOWN)
INPUT_ONLY_PINS = [34, 35, 36, 39]
# 1(TX0), 3(RX0) dùng cho USB serial
SERIAL_PINS = [1, 3]

# ===================== QUẢN LÝ CẤU HÌNH =====================
def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    except:
        return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f)

config = load_config()

# Các biến trạng thái hoạt động toàn cục
modbus_value = config.get('last_value', config['value_off'])
if modbus_value not in (config['value_on'], config['value_off']):
    modbus_value = config['value_off']
last_btn_state = 0
write_pending = False
desired_value = 0
fail_count = 0
modbus_connected = True
confirmed_value = None        # Giá trị đã được xác nhận qua Modbus (None = chưa có)
health_pending = False        # Đang chờ phản hồi health check?
last_health_time = 0          # Thời gian health check gần nhất
last_write_time = 0           # Thời gian gửi lệnh ghi cuối cùng
last_ntp_time = 0             # Thời gian đồng bộ NTP gần nhất
ntp_retry_count = 0           # Số lần thử NTP thất bại liên tiếp
last_led_toggle_time = 0      # Thời gian đảo trạng thái LED gần nhất (non-blocking)
HEALTH_INTERVAL = 10000       # 10 giây
WRITE_TIMEOUT = 3000          # 3 giây timeout chờ ghi
NTP_INTERVAL = 86400000       # 24 giờ (ms) giữa các lần đồng bộ NTP
NTP_RETRY_INTERVAL = 300000   # 5 phút (ms) thử lại NTP khi thất bại


# ===================== LOG BUFFER (200 dòng gần nhất) =====================
LOG_MAX = 200
log_buffer = []

def add_log(msg):
    """Thêm dòng log kèm thời gian, giữ tối đa LOG_MAX dòng"""
    global log_buffer
    try:
        now = time.localtime()
        ts = '{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}'.format(now[0], now[1], now[2], now[3], now[4], now[5])
        entry = '[{}] {}'.format(ts, msg)
        log_buffer.append(entry)
        if len(log_buffer) > LOG_MAX:
            log_buffer = log_buffer[-LOG_MAX:]
        print(msg)
    except:
        print(msg)

def get_logs_json():
    """Trả về JSON của các dòng log"""
    return json.dumps(log_buffer)

# Biến trạng thái kết nối BLE và cờ đồng bộ
# (BLE service/characteristic đã được khởi tạo ở đầu file)
ble_connected = False
ble_sync_pending = False
ble_active = ble_service is not None  # False nếu BLE register thất bại

# Tác vụ quảng bá BLE (Advertising)
async def peripheral_task():
    global ble_connected, ble_sync_pending
    # Đợi hệ thống ổn định + giải phóng RAM trước khi chiếm bộ nhớ BLE advertising
    await asyncio.sleep_ms(3000)
    gc.collect()
    add_log("Khởi động BLE quảng bá...")
    while True:
        try:
            gc.collect()  # Dọn rác trước mỗi lần advertise
            async with await aioble.advertise(
                250_000, # 250ms interval
                name="Solis-EPM-Bluetooth",
                services=[_NUS_SERVICE_UUID],
            ) as connection:
                add_log("BLE Central connected from: {}".format(connection.device))
                ble_connected = True
                ble_sync_pending = True
                await connection.disconnected()
                ble_connected = False
                add_log("BLE Central disconnected")
        except asyncio.CancelledError:
            break
        except Exception as e:
            add_log(f"Lỗi trong BLE peripheral_task: {e}")
            # Không thử khởi tạo lại BLE thủ công - để aioble tự xử lý
            gc.collect()
        await asyncio.sleep_ms(1000)

# ===================== HÀM BLE TRANSMIT CHUNKS =====================

def _ble_send_chunks(text):
    global ble_connected, tx_characteristic
    if not ble_connected or tx_characteristic is None:
        return
    try:
        full_text = text + '\n'
        raw_bytes = full_text.encode('utf-8')
        chunk_size = 20
        for i in range(0, len(raw_bytes), chunk_size):
            chunk = raw_bytes[i:i + chunk_size]
            tx_characteristic.write(chunk, send_update=True)
            time.sleep_ms(15)
    except Exception as e:
        add_log(f"Lỗi gửi BLE chunks: {e}")

def _ble_send_json(obj):
    """Gửi JSON object qua BLE TX characteristic phân mảnh"""
    try:
        payload = json.dumps(obj)
        _ble_send_chunks(payload)
    except Exception as e:
        add_log(f'BLE send JSON lỗi: {e}')

# ===================== Tác vụ BLE RX =====================
async def ble_rx_task():
    global config, modbus_value, write_pending, desired_value, last_write_time, ble_sync_pending
    ble_rx_buffer = ""
    while True:
        try:
            connection, data = await rx_characteristic.written()
            if data:
                try:
                    chunk = data.decode('utf-8')
                    ble_rx_buffer += chunk
                    
                    if '\n' in ble_rx_buffer:
                        lines = ble_rx_buffer.split('\n')
                        ble_rx_buffer = lines[-1]
                        
                        for line in lines[:-1]:
                            msg_str = line.strip()
                            if not msg_str:
                                continue
                            add_log(f"BLE nhận: {msg_str}")
                            if msg_str in ("on", "off", "sync"):
                                if msg_str == 'sync':
                                    ble_sync_pending = True
                                else:
                                    if msg_str == 'on':
                                        modbus_value = config['value_on']
                                    else:
                                        modbus_value = config['value_off']
                                    config['last_value'] = modbus_value
                                    save_config(config)
                                    add_log(f'BLE cmd -> Slave {config["slave_id"]} Reg {config["reg_addr"]} = {modbus_value}')
                                    uart.read()
                                    write_modbus_register(config['slave_id'], config['reg_addr'], modbus_value)
                                    desired_value = modbus_value
                                    write_pending = True
                                    last_write_time = time.ticks_ms()
                            else:
                                try:
                                    data_json = json.loads(msg_str)
                                    cmd_type = data_json.get("type")

                                    # ---- Đồng bộ trạng thái theo yêu cầu ----
                                    if cmd_type == "sync":
                                        ble_sync_pending = True

                                    # ---- Lưu cấu hình Modbus + Phím ----
                                    elif cmd_type == "save":
                                        if 'slave_id' in data_json:
                                            config['slave_id'] = int(data_json['slave_id'])
                                        if 'reg_addr' in data_json:
                                            config['reg_addr'] = int(data_json['reg_addr'])
                                        if 'value_on' in data_json:
                                            config['value_on'] = int(data_json['value_on'])
                                        if 'value_off' in data_json:
                                            config['value_off'] = int(data_json['value_off'])
                                        if 'health_reg' in data_json:
                                            config['health_reg'] = int(data_json['health_reg'])
                                        
                                        changed_btn = False
                                        if 'btn_pin' in data_json:
                                            config['btn_pin'] = int(data_json['btn_pin'])
                                            changed_btn = True
                                        if 'btn_mode' in data_json:
                                            config['btn_mode'] = data_json['btn_mode']
                                            changed_btn = True
                                        if 'btn_type' in data_json:
                                            config['btn_type'] = data_json['btn_type']
                                        
                                        save_config(config)
                                        
                                        if changed_btn:
                                            global btn
                                            b_mode = PIN_MODES.get(config.get('btn_mode', 'PULL_UP'), Pin.PULL_UP)
                                            btn = Pin(config['btn_pin'], Pin.IN, b_mode)
                                            add_log(f'Khởi tạo lại nút nhấn: GPIO {config["btn_pin"]} mode {config["btn_mode"]}')
                                            
                                        add_log('Cấu hình đã lưu qua BLE: {}'.format(config))
                                        ble_sync_pending = True

                                    # ---- Đồng bộ thời gian ----
                                    elif cmd_type == "time":
                                        if 'epoch' in data_json:
                                            epoch = int(data_json['epoch'])
                                            local_epoch = epoch + 7 * 3600
                                            t2 = time.localtime(local_epoch)
                                            import machine as m2
                                            rtc = m2.RTC()
                                            rtc.datetime((t2[0], t2[1], t2[2], t2[6], t2[3], t2[4], t2[5], 0))
                                            add_log(f"Đồng bộ thời gian BLE: {t2[0]}-{t2[1]:02d}-{t2[2]:02d} {t2[3]:02d}:{t2[4]:02d}:{t2[5]:02d}")



                                except Exception as e:
                                    add_log(f"Lỗi parse JSON BLE: {e}")
                except Exception as e:
                    add_log(f"Lỗi xử lý dữ liệu BLE: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            add_log(f"Lỗi trong ble_rx_task: {e}")
            await asyncio.sleep_ms(500)

# ===================== GPIO + NÚT NHẤN (UART/LED đã init ở Bước 2) =====================
PIN_MODES = {'PULL_UP': Pin.PULL_UP, 'PULL_DOWN': Pin.PULL_DOWN}
btn_mode = PIN_MODES.get(config['btn_mode'], Pin.PULL_UP)
btn = Pin(config['btn_pin'], Pin.IN, btn_mode)

# ===================== HÀM MODBUS =====================
def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, 'little')

def write_modbus_register(slave_id, reg_addr, value):
    frame = bytearray()
    frame.append(slave_id)
    frame.append(0x06)
    frame.append((reg_addr >> 8) & 0xFF)
    frame.append(reg_addr & 0xFF)
    frame.append((value >> 8) & 0xFF)
    frame.append(value & 0xFF)
    frame.extend(crc16(frame))
    uart.write(frame)

def wait_for_response(timeout_ms=500, min_len=5):
    """Đọc phản hồi Modbus với cơ chế character timeout
       - min_len: số byte tối thiểu trước khi bắt đầu chờ end-of-frame
       - Sau khi đủ min_len, chờ thêm 30ms nếu không có byte mới → coi như hết frame
     """
    buf = bytearray()
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
        if uart.any():
            buf.extend(uart.read())
        if len(buf) >= min_len:
            # Chờ thêm 30ms để gom hết các byte còn lại (tránh RX phân mảnh)
            time.sleep_ms(30)
            if not uart.any():
                return buf
        time.sleep_ms(10)
    return buf if buf else None

def verify_response(frame, slave_id, reg_addr, value):
    if frame is None or len(frame) < 8:
        return False
    calc_crc = crc16(frame[:6])
    if frame[6] != calc_crc[0] or frame[7] != calc_crc[1]:
        return False
    if frame[0] != slave_id or frame[1] != 0x06:
        return False
    if frame[2] != ((reg_addr >> 8) & 0xFF) or frame[3] != (reg_addr & 0xFF):
        return False
    if frame[4] != ((value >> 8) & 0xFF) or frame[5] != (value & 0xFF):
        return False
    return True

def read_modbus_register(slave_id, reg_addr):
    """Gửi lệnh đọc Input Register (FC=04) cho 3X"""
    frame = bytearray()
    frame.append(slave_id)
    frame.append(0x04)  # Function Code: Read Input Registers (3X)
    frame.append((reg_addr >> 8) & 0xFF)
    frame.append(reg_addr & 0xFF)
    frame.append(0x00)  # Số lượng register: high byte
    frame.append(0x01)  # Số lượng register: low byte (1 register)
    frame.extend(crc16(frame))
    uart.read()  # Xóa bộ đệm RX trước khi gửi
    uart.write(frame)

def verify_read_response(frame, slave_id, reg_addr):
    """Kiểm tra phản hồi đọc FC04 (Input Register 3X)
       Format chuẩn 7 bytes: [slave, 0x04, 0x02, data_hi, data_lo, crc_lo, crc_hi]
       Một số thiết bị có thể trả thêm byte → kiểm tra linh hoạt"""
    if frame is None or len(frame) < 5:
        return False
    # Kiểm tra Slave ID và Function Code
    if frame[0] != slave_id or frame[1] != 0x04:
        return False
    # Xác định vị trí CRC: 2 bytes cuối của frame
    crc_pos = len(frame) - 2
    calc_crc = crc16(frame[:crc_pos])
    if frame[crc_pos] != calc_crc[0] or frame[crc_pos + 1] != calc_crc[1]:
        return False
    return True  # Đọc thành công






add_log('Modbus config loaded: {}'.format(config))
add_log('Hệ thống khởi động - EPM Modbus Control with BLE & WiFi')

# ===================== WATCHDOG TIMER (Chống treo ESP32) =====================
# WDT đã được khởi tạo ở Bước 2 (cùng BLE + hardware, heap sạch)

# Khởi động dịch vụ BLE UART qua aioble
last_ble_sync_time = 0
BLE_SYNC_INTERVAL = 5000  # 5 giây

# BLE đã được khởi tạo ở đầu file (sau imports) - giống test file

# ===================== WIFI: DECOMMISSIONED =====================
gc.collect()

# LED tắt cho đến khi có xác nhận Modbus thành công
led1.value(0)
led2.value(0)

# Gửi lại giá trị vận hành trước đó khi khởi động
add_log(f'Khôi phục: gửi giá trị {modbus_value} khi khởi động...')
uart.read()
write_modbus_register(config['slave_id'], config['reg_addr'], modbus_value)
add_log(f'Đã gửi: Slave {config["slave_id"]} Reg {config["reg_addr"]} = {modbus_value}')

# Chờ phản hồi Modbus (blocking) trước khi làm tiếp
resp = wait_for_response(timeout_ms=1000)
if verify_response(resp, config['slave_id'], config['reg_addr'], modbus_value):
    add_log('Ghi thành công!')
    confirmed_value = modbus_value
    if confirmed_value == config['value_on']:
        led1.value(1); led2.value(0)
    else:
        led1.value(0); led2.value(1)
else:
    add_log('Chưa có phản hồi khôi phục, sẽ thử lại trong health check')







def send_ble_sync():
    global ble_sync_pending, last_ble_sync_time
    if ble_connected:
        sync_data = {
            "type": "sync",
            "slave_id": config['slave_id'],
            "reg_addr": config['reg_addr'],
            "health_reg": config.get('health_reg', 36017),
            "value_on": config['value_on'],
            "value_off": config['value_off'],
            "cur_val": modbus_value,
            "connected": modbus_connected,
            "btn_pin": config['btn_pin'],
            "btn_mode": config.get('btn_mode', 'PULL_UP'),
            "btn_type": config.get('btn_type', 'momentary')
        }
        try:
            payload = json.dumps(sync_data)
            _ble_send_chunks(payload)
            last_ble_sync_time = time.ticks_ms()
            ble_sync_pending = False
        except Exception as e:
            add_log(f"Lỗi gửi sync BLE: {e}")



# ===================== MAIN LOOP =====================
async def main_loop():
    global config, btn, modbus_value, write_pending, desired_value, fail_count, modbus_connected, log_buffer, confirmed_value, ble_sync_pending, last_btn_state, last_led_toggle_time, health_pending, last_health_time, last_write_time
        
    while True:
        # Feed watchdog
        wdt.feed()
        
        now = time.ticks_ms()
        

        
        # 2. Đọc nút nhấn (PULL_UP: nhấn=0; PULL_DOWN: nhấn=1)
        btn_state = btn.value()
        btn_type = config.get('btn_type', 'momentary')
        
        if btn_type == 'toggle':
            if config.get('btn_mode') == 'PULL_UP':
                switch_on = (btn_state == 0)
            else:
                switch_on = (btn_state == 1)
            new_value = config['value_on'] if switch_on else config['value_off']
            if new_value != modbus_value:
                modbus_value = new_value
                config['last_value'] = modbus_value
                save_config(config)
                add_log(f'Công tắc -> Slave {config["slave_id"]} Reg {config["reg_addr"]} = {modbus_value}')
                uart.read()
                write_modbus_register(config['slave_id'], config['reg_addr'], modbus_value)
                desired_value = modbus_value
                write_pending = True
                last_write_time = time.ticks_ms()
        else:
            if config.get('btn_mode') == 'PULL_UP':
                is_press = (btn_state == 0 and last_btn_state == 1)
            else:
                is_press = (btn_state == 1 and last_btn_state == 0)
            if is_press:
                if modbus_value == config['value_off']:
                    modbus_value = config['value_on']
                else:
                    modbus_value = config['value_off']
                config['last_value'] = modbus_value
                save_config(config)
                add_log(f'Nhấn nút -> Slave {config["slave_id"]} Reg {config["reg_addr"]} = {modbus_value}')
                uart.read()
                write_modbus_register(config['slave_id'], config['reg_addr'], modbus_value)
                desired_value = modbus_value
                write_pending = True
                last_write_time = time.ticks_ms()
                await asyncio.sleep_ms(50)
        last_btn_state = btn_state
        
        # 3. Xử lý các lệnh Modbus đang chờ phản hồi
        if write_pending:
            if time.ticks_diff(now, last_write_time) >= WRITE_TIMEOUT:
                add_log('Timeout chờ ghi, chuyển sang health check...')
                write_pending = False
                if fail_count >= 3 and modbus_connected:
                    modbus_connected = False
                    led1.value(0)
                    led2.value(0)
                    add_log('MẤT KẾT NỐI MODBUS!')
                    ble_sync_pending = True
            else:
                resp = wait_for_response(timeout_ms=500)
                if verify_response(resp, config['slave_id'], config['reg_addr'], desired_value):
                    add_log('Ghi thành công!')
                    fail_count = 0
                    confirmed_value = desired_value
                    if confirmed_value == config['value_on']:
                        led1.value(1); led2.value(0)
                    else:
                        led1.value(0); led2.value(1)
                    if not modbus_connected:
                        modbus_connected = True
                        add_log('Modbus đã kết nối lại!')
                    ble_sync_pending = True
                    write_pending = False
                    health_pending = False
                    last_health_time = time.ticks_ms()
                else:
                    fail_count += 1
                    add_log(f'Chưa có phản hồi ({fail_count}/10), sẽ thử lại...')
                    if fail_count >= 200:
                        add_log('QUÁ 200 LẦN MẤT KẾT NỐI! RESET THIẾT BỊ...')
                        await asyncio.sleep_ms(500)
                        machine.reset()
                    if fail_count >= 3 and modbus_connected:
                        modbus_connected = False
                        led1.value(0)
                        led2.value(0)
                        add_log('MẤT KẾT NỐI MODBUS!')
                        ble_sync_pending = True
                    await asyncio.sleep_ms(200)
                    
        elif health_pending:
            resp = wait_for_response(timeout_ms=500)
            if verify_read_response(resp, config['slave_id'], config.get('health_reg', 36017)):
                health_pending = False
                last_health_time = time.ticks_ms()
                fail_count = 0
                if not modbus_connected:
                    modbus_connected = True
                    if confirmed_value == config['value_on']:
                        led1.value(1); led2.value(0)
                    elif confirmed_value == config['value_off']:
                        led1.value(0); led2.value(1)
                    else:
                        led1.value(0); led2.value(0)
                    add_log('Modbus đã kết nối lại!')
                    ble_sync_pending = True
            else:
                health_pending = False
                fail_count += 1
                add_log(f'Health check thất bại ({fail_count}/10)')
                if fail_count >= 200:
                    add_log('QUÁ 200 LẦN MẤT KẾT NỐI! RESET THIẾT BỊ...')
                    await asyncio.sleep_ms(500)
                    machine.reset()
                if fail_count >= 3 and modbus_connected:
                    modbus_connected = False
                    led1.value(0)
                    led2.value(0)
                    add_log('MẤT KẾT NỐI MODBUS!')
                    ble_sync_pending = True
                    
        else:
            if time.ticks_diff(now, last_health_time) >= HEALTH_INTERVAL:
                read_modbus_register(config['slave_id'], config.get('health_reg', 36017))
                health_pending = True
                last_health_time = now
                

                
        # 4. Nhấp nháy LED theo trạng thái (non-blocking)
        if modbus_connected:
            led_interval = 1000 if modbus_value == config['value_on'] else 200
        else:
            led_interval = 500
        if time.ticks_diff(now, last_led_toggle_time) >= led_interval:
            led.toggle()
            last_led_toggle_time = now
            
        # 6. Gửi dữ liệu sync BLE định kỳ hoặc khi có yêu cầu
        if ble_sync_pending or (ble_connected and time.ticks_diff(now, last_ble_sync_time) >= BLE_SYNC_INTERVAL):
            send_ble_sync()
            
        # Nhường CPU cho hệ thống chạy ổn định và tiết kiệm năng lượng
        await asyncio.sleep_ms(20)

# Tác vụ chạy bất đồng bộ chính (Async Main)
async def main_async():
    # Tạo task theo thứ tự ưu tiên: main_loop trước, BLE sau
    t_loop = asyncio.create_task(main_loop())
    t_peripheral = asyncio.create_task(peripheral_task())
    t_rx = asyncio.create_task(ble_rx_task())
    await asyncio.gather(t_loop, t_peripheral, t_rx)

# Chạy chương trình - GC mạnh trước khi vào asyncio
gc.collect()
add_log(f'RAM trống: {gc.mem_free()} bytes')
try:
    asyncio.run(main_async())
except MemoryError:
    add_log('MEMORY ERROR! Thử lại với GC mạnh hơn...')
    gc.collect()
    time.sleep_ms(500)
    try:
        asyncio.run(main_async())
    except Exception as e2:
        add_log(f"Lỗi nghiêm trọng lần 2: {e2}")
except Exception as e:
    add_log(f"Lỗi nghiêm trọng trong asyncio event loop: {e}")