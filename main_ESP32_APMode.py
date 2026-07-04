import sys
import time
import json
import network
import socket
import machine

try:
    from machine import Pin, UART, WDT
except ImportError:
    print("This example must be run with a MicroPython interpreter, such as on an ESP32 board.")
    sys.exit(1)

# ===================== WATCHDOG TIMER (Chống treo ESP32) =====================
# WDT tự động reset ESP32 nếu không được feed() trong thời gian timeout.
# Đặt timeout 10 giây: đủ để main loop chạy qua mọi blocking operation.
# Nếu main loop bị treo (deadlock, infinite loop, memory corruption...),
# WDT sẽ reset toàn bộ hệ thống, khôi phục hoạt động.
WDT_TIMEOUT_MS = 10000  # 10 giây

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

# ===================== KHỞI TẠO PHẦN CỨNG =====================
uart = UART(1, baudrate=9600, bits=8, parity=None, stop=1, tx=17, rx=16)
led = Pin(2, Pin.OUT)    # LED nhấp nháy báo trạng thái
led1 = Pin(15, Pin.OUT)  # LED báo ON
led2 = Pin(4, Pin.OUT)   # LED báo OFF
PIN_MODES = {'PULL_UP': Pin.PULL_UP, 'PULL_DOWN': Pin.PULL_DOWN}
btn_mode = PIN_MODES.get(config['btn_mode'], Pin.PULL_UP)
btn = Pin(config['btn_pin'], Pin.IN, btn_mode)

add_log('Modbus config loaded: {}'.format(config))
add_log('Hệ thống khởi động - WiFi AP: Solis EPM ZeroExport ON-OFF')

# ===================== WATCHDOG TIMER (Chống treo ESP32) =====================
# WDT tự động reset ESP32 nếu không được feed() trong thời gian timeout.
# Đặt timeout 10 giây: đủ để main loop chạy qua mọi blocking operation.
# Nếu main loop bị treo (deadlock, infinite loop, memory corruption...),
# WDT sẽ reset toàn bộ hệ thống, khôi phục hoạt động.
wdt = WDT(timeout=WDT_TIMEOUT_MS)
add_log(f'Watchdog Timer khởi tạo: {WDT_TIMEOUT_MS//1000} giây')

# ===================== WIFI ACCESS POINT =====================
ap = network.WLAN(network.AP_IF)
ap.active(True)
ap.config(essid='Solis EPM ZeroExport ON-OFF', password='19001006', authmode=network.AUTH_WPA2_PSK)
ap.ifconfig(('192.168.88.1', '255.255.255.0', '192.168.88.1', '8.8.8.8'))
add_log('WiFi AP: Solis EPM ZeroExport ON-OFF | PW: 19001006 | IP: 192.168.88.1')

# ===================== WIFI STATION (CLIENT) =====================
sta = network.WLAN(network.STA_IF)
sta.active(True)

def wifi_load_config():
    try:
        with open(WIFI_CONFIG_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"ssid": "", "password": ""}

def wifi_save_config(ssid, password):
    with open(WIFI_CONFIG_FILE, 'w') as f:
        json.dump({"ssid": ssid, "password": password}, f)

def wifi_scan():
    """Quét các mạng WiFi xung quanh"""
    try:
        nets = sta.scan()
        result = []
        seen = set()
        for net in nets:
            ssid = net[0].decode()
            if ssid and ssid not in seen:
                seen.add(ssid)
                result.append({'ssid': ssid, 'rssi': net[3], 'auth': net[4]})
        result.sort(key=lambda x: x['rssi'], reverse=True)
        return result
    except Exception as e:
        add_log(f'Lỗi quét WiFi: {e}')
        return []

def wifi_connect(ssid, password):
    """Kết nối tới mạng WiFi"""
    if not ssid:
        return False
    add_log(f'Đang kết nối WiFi: {ssid}...')
    sta.active(True)
    if sta.isconnected():
        sta.disconnect()
        time.sleep_ms(500)
    sta.connect(ssid, password)
    timeout = 15000
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < timeout:
        if sta.isconnected():
            info = sta.ifconfig()
            add_log(f'WiFi đã kết nối! IP: {info[0]}')
            wifi_save_config(ssid, password)
            sync_ntp_time()
            return True
        time.sleep_ms(500)
    add_log(f'Không thể kết nối WiFi: {ssid}')
    return False

def wifi_disconnect():
    """Ngắt kết nối WiFi và xóa cấu hình"""
    sta.disconnect()
    wifi_save_config("", "")
    add_log('Đã ngắt WiFi và xóa cấu hình')

def sync_ntp_time():
    """Đồng bộ thời gian GMT+7 qua NTP"""
    global last_ntp_time, ntp_retry_count
    if not sta.isconnected():
        return False
    try:
        import ntptime
        ntptime.settime()
        # Cộng 7 giờ cho GMT+7
        t = time.localtime()
        epoch = time.mktime(t) + 7 * 3600
        t2 = time.localtime(epoch)
        import machine as m2
        rtc = m2.RTC()
        rtc.datetime((t2[0], t2[1], t2[2], t2[6], t2[3], t2[4], t2[5], 0))
        add_log('Đã đồng bộ thời gian GMT+7 qua NTP')
        last_ntp_time = time.ticks_ms()
        ntp_retry_count = 0
        return True
    except Exception as e:
        ntp_retry_count += 1
        add_log(f'NTP thất bại lần {ntp_retry_count}: {e}')
        return False

# ===================== WEBSERVER =====================
server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_sock.bind(('0.0.0.0', 80))
server_sock.listen(1)
server_sock.settimeout(0.3)  # Non-blocking với timeout ngắn

HTML_PAGE = """\
HTTP/1.0 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n\
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Modbus Toggle Config</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Arial,sans-serif;background:#1a1a2e;color:#eee;padding:20px;max-width:500px;margin:auto}}
h1{{color:#e94560;text-align:center;margin-bottom:20px;font-size:22px}}
.group{{background:#16213e;border-radius:10px;padding:15px;margin-bottom:15px}}
.group h2{{font-size:16px;color:#0f3460;background:#e94560;display:inline-block;padding:3px 12px;border-radius:5px;margin-bottom:12px}}
label{{display:block;font-size:14px;margin:8px 0 3px;color:#aaa}}
input,select{{width:100%;padding:10px;border:none;border-radius:6px;background:#0f3460;color:#fff;font-size:15px;outline:none}}
input:focus,select:focus{{outline:2px solid #e94560}}
select option{{background:#16213e}}
.btn-save{{width:100%;padding:12px;background:#e94560;color:#fff;font-size:16px;font-weight:bold;border:none;border-radius:8px;cursor:pointer;margin-top:5px}}
.btn-save:hover{{background:#c73650}}
.btn-sm{{width:100%;padding:8px;color:#fff;font-size:13px;font-weight:bold;border:none;border-radius:6px;cursor:pointer;margin-top:5px}}
.test-row{{display:flex;gap:10px;margin-top:10px}}
.test-btn{{flex:1;padding:12px;font-size:16px;font-weight:bold;border:none;border-radius:8px;cursor:pointer;color:#fff}}
#log_container{{scrollbar-width:thin;scrollbar-color:#e94560 #0a0a1a}}
#log_container::-webkit-scrollbar{{width:6px}}
#log_container::-webkit-scrollbar-thumb{{background:#e94560;border-radius:3px}}
.test-on{{background:#27ae60}}
.test-on:hover{{background:#219a52}}
.test-off{{background:#e74c3c}}
.test-off:hover{{background:#c0392b}}
.led-row{{display:flex;align-items:center;margin:6px 0;font-size:14px}}
.led-dot{{display:inline-block;width:14px;height:14px;border-radius:50%;margin-right:10px;flex-shrink:0}}
.led-dot.on{{background:#2ecc71;box-shadow:0 0 10px #2ecc71}}
.led-dot.off{{background:#555}}
.status{{text-align:center;font-size:13px;color:#aaa;margin-top:10px}}
#test_result{{margin-top:8px;font-size:13px;color:#aaa;text-align:center;min-height:20px}}
.big-status{{flex:0 0 130px;border:3px solid #e94560;border-radius:12px;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:12px 8px;background:#0f3460}}
.big-status-label{{font-size:11px;color:#888;margin-bottom:4px;letter-spacing:1px}}
.big-status-value{{font-size:32px;font-weight:bold;line-height:1.2}}
.big-status.on .big-status-value{{color:#2ecc71;text-shadow:0 0 15px #2ecc71}}
.big-status.off .big-status-value{{color:#e74c3c;text-shadow:0 0 15px #e74c3c}}
.big-status.disconnected .big-status-value{{color:#555}}
</style>
</head>
<body>
<h1>&#9881; Modbus Toggle Config</h1>

<div class="group">
<h2>Tr&#7841;ng th&aacute;i</h2>
<div style="display:flex;gap:15px;align-items:stretch">
  <div style="flex:1;min-width:0">
    <div class="led-row"><span class="led-dot {led1_init_class}" id="dot1"></span>LED ON (GPIO 15): <span id="txt1" style="margin-left:6px;font-weight:bold">{led1_init_text}</span></div>
    <div class="led-row"><span class="led-dot {led2_init_class}" id="dot2"></span>LED OFF (GPIO 4): <span id="txt2" style="margin-left:6px;font-weight:bold">{led2_init_text}</span></div>
    <div style="margin-top:6px;font-size:14px;color:#aaa">Gi&aacute; tr&#7883;: <span id="cur_val" style="color:#fff;font-weight:bold">{cur_val_init}</span><span id="cur_state" style="display:none"></span></div>
    <div id="conn_status" style="margin-top:8px;padding:6px 10px;border-radius:6px;font-size:13px;font-weight:bold;text-align:center;background:#1e824c;color:#fff">&#9679; K&#7871;t n&#7889;i Modbus OK</div>
  </div>
  <div class="big-status {big_init_class}" id="big_status">
    <div class="big-status-label">TR&#7840;NG TH&Aacute;I</div>
    <div class="big-status-value" id="cur_state_big" style="color:{big_init_color};text-shadow:{big_init_shadow}">{big_init_text}</div>
  </div>
</div>
</div>

<div class="group">
<h2>Ki&#7875;m tra</h2>
<div class="test-row">
<button class="test-btn test-on" onclick="sendCmd('on')">&#9654; B&#7852;T (ON)</button>
<button class="test-btn test-off" onclick="sendCmd('off')">&#9632; T&#7854;T (OFF)</button>
</div>
<div id="test_result"></div>
</div>

<div class="group">
<h2>WiFi Client</h2>
<div id="wifi_status" style="margin-bottom:10px;padding:6px 10px;border-radius:6px;font-size:13px;font-weight:bold;text-align:center;background:#555;color:#fff">&#9679; &#272;ang ki&#7875;m tra...</div>
<div class="test-row">
<button class="test-btn" style="background:#3498db;flex:1" onclick="scanWifi()">&#128269; Qu&eacute;t WiFi</button>
</div>
<div id="wifi_list" style="margin-top:8px;display:none">
<label>Ch&#7885;n m&#7841;ng</label>
<select id="wifi_ssid" style="margin-bottom:8px"></select>
<label>M&#7853;t kh&#7849;u</label>
<input type="password" id="wifi_password" placeholder="Nh&#7853;p m&#7853;t kh&#7849;u WiFi">
<button class="btn-save" style="margin-top:8px" onclick="connectWifi()">&#128279; K&#7871;t n&#7889;i</button>
<button class="btn-sm" style="background:#e74c3c" onclick="forgetWifi()">&#128465; Qu&ecirc;n m&#7841;ng</button>
<div id="wifi_result" style="margin-top:8px;font-size:13px;color:#aaa;text-align:center;min-height:20px"></div>
</div>
</div>

<form method="POST" action="/save">
<div class="group">
<h2>Modbus</h2>
<label>Slave ID</label>
<input type="number" name="slave_id" value="{slave_id}" min="1" max="247">
<label>Register Address (Write)</label>
<input type="number" name="reg_addr" value="{reg_addr}" min="0" max="65535">
<label>HealthCheck Reg (Read)</label>
<input type="number" name="health_reg" value="{health_reg}" min="0" max="65535">
</div>
<div class="group">
<h2>Toggle Values</h2>
<label>Value 1 (ON)</label>
<input type="number" name="value_on" value="{value_on}">
<label>Value 2 (OFF)</label>
<input type="number" name="value_off" value="{value_off}">
</div>
<div class="group">
<h2>Button Pin</h2>
<label>Ch&#7885;n ch&acirc;n n&uacute;t nh&#7845;n</label>
<select name="btn_pin">{pin_options}</select>
<label>Ch&#7871; &#273;&#7897; k&eacute;o</label>
<select name="btn_mode">
<option value="PULL_UP"{sel_up}>PULL_UP (nh&#7845;n = 0, th&#432;&#7901;ng = 1)</option>
<option value="PULL_DOWN"{sel_down}>PULL_DOWN (nh&#7845;n = 1, th&#432;&#7901;ng = 0)</option>
</select>
<label>Lo&#7841;i n&uacute;t nh&#7845;n</label>
<select name="btn_type">
<option value="momentary"{sel_momentary}>N&uacute;t nh&#7845;n nh&#7843; (xung ng&#7855;n)</option>
<option value="toggle"{sel_toggle}>C&ocirc;ng t&#7855;c gi&#7919; tr&#7841;ng th&aacute;i ON/OFF</option>
</select>
</div>
<button class="btn-save" type="submit">&#128190; L&#432;u c&agrave;i &#273;&#7863;t</button>
</form>

<div class="group" id="log_group">
<h2>Log kết nối</h2>
<div id="log_container" style="background:#0a0a1a;border-radius:6px;padding:10px;max-height:300px;overflow-y:auto;font-family:'Courier New',monospace;font-size:11px;line-height:1.5;color:#0f0">
<div class="log-line" style="color:#666">&#9654; &#272;ang t&#7843;i log...</div>
</div>
<div style="text-align:right;margin-top:5px">
<button onclick="clearLogs()" style="background:#555;color:#fff;border:none;border-radius:4px;padding:4px 12px;font-size:12px;cursor:pointer">&#128465; X&oacute;a log</button>
</div>
</div>

<div class="status">AP: Solis EPM / 192.168.88.1 | {wifi_ap_status}</div>

<script>
function upd() {{
  fetch('/status').then(function(r){{return r.json()}}).then(function(d){{
    var dot1=document.getElementById('dot1'), dot2=document.getElementById('dot2');
    var t1=document.getElementById('txt1'), t2=document.getElementById('txt2');
    dot1.className='led-dot'+(d.led1?' on':' off');
    dot2.className='led-dot'+(d.led2?' on':' off');
    t1.textContent=d.led1?'ON':'OFF';
    t2.textContent=d.led2?'ON':'OFF';
    document.getElementById('cur_val').textContent=d.value;
    var csEl=document.getElementById('cur_state');
    if(csEl) csEl.textContent=d.state;
    try{{
      var sv=document.getElementById('cur_state_big');
      if(d.connected && d.value==d.val_on){{sv.textContent='ON';sv.style.color='#2ecc71';sv.style.textShadow='0 0 15px #2ecc71';}}
      else if(d.connected && d.value==d.val_off){{sv.textContent='OFF';sv.style.color='#e74c3c';sv.style.textShadow='0 0 15px #e74c3c';}}
      else{{sv.textContent='--';sv.style.color='#555';sv.style.textShadow='none';}}
    }}catch(e){{}}
    var cs=document.getElementById('conn_status');
    if(d.connected){{
      cs.innerHTML='\\u25cf K\\u1ebft n\\u1ed1i Modbus OK';
      cs.style.background='#1e824c';
    }}else{{
      cs.innerHTML='\\u26a0 M\\u1ea4T K\\u1ebeT N\\u1ed0I Modbus!';
      cs.style.background='#c0392b';
    }}
    var ws=document.getElementById('wifi_status');
    if(d.wifi_connected){{
      ws.innerHTML='\\u25cf ' + d.wifi_ssid + ' (' + d.wifi_ip + ')';
      ws.style.background='#1e824c';
    }}else if(d.wifi_saved){{
      ws.innerHTML='\\u26a0 \\u0110&atilde; l&#432;u ' + d.wifi_saved + ' nh&#432;ng ch&#432;a k\\u1ebft n\\u1ed1i';
      ws.style.background='#d35400';
    }}else{{
      ws.innerHTML='\\u25cb Ch&#432;a c\\u1ea5u h&igrave;nh WiFi';
      ws.style.background='#555';
    }}
  }}).catch(function(){{}});
}}
function sendCmd(v) {{
document.getElementById('test_result').textContent='\u0110ang g\u1EEDi...';
  fetch('/cmd?value='+v).then(function(r){{return r.json()}}).then(function(d){{
    document.getElementById('test_result').textContent=(d.success?'\\u2713 ':'\\u2717 ')+d.msg;
    if(d.success){{
      var sv=document.getElementById('cur_state_big');
      if(v=='on'){{sv.textContent='ON';sv.style.color='#2ecc71';sv.style.textShadow='0 0 15px #2ecc71';}}
      else{{sv.textContent='OFF';sv.style.color='#e74c3c';sv.style.textShadow='0 0 15px #e74c3c';}}
    }}
    upd();
  }}).catch(function(){{
    document.getElementById('test_result').textContent='\u2717 L\u1ED7i k\u1ebft n\u1ed1i';
  }});
}}
function updLogs() {{
  fetch('/logs').then(function(r){{return r.json()}}).then(function(logs){{
    var c=document.getElementById('log_container');
    if(!c) return;
    var html='';
    for(var i=Math.max(0,logs.length-200);i<logs.length;i++){{
      html+='<div class="log-line">'+logs[i].replace(/</g,'&lt;')+'</div>';
    }}
    c.innerHTML=html;
    c.scrollTop=c.scrollHeight;
  }}).catch(function(){{
    var c=document.getElementById('log_container');
    if(c) c.innerHTML='<div class="log-line" style="color:#e74c3c">&#10060; L&#7895;i t\\u1ea3i log</div>';
  }});
}}
function clearLogs() {{
  fetch('/clearlogs').then(function(){{
    document.getElementById('log_container').innerHTML='<div class="log-line" style="color:#666">&#9654; Log \\u0111&atilde; x&oacute;a</div>';
  }});
}}
function scanWifi() {{
document.getElementById('wifi_result').textContent='\u0110ang qu\u00E9t...';
  fetch('/wifi/scan').then(function(r){{return r.json()}}).then(function(d){{
    var sel=document.getElementById('wifi_ssid');
    sel.innerHTML='';
    if(d.length===0){{
      sel.innerHTML='<option>Kh&ocirc;ng t&igrave;m th&#7845;y m&#7841;ng</option>';
    }}else{{
      for(var i=0;i<d.length;i++){{
        var opt=document.createElement('option');
        opt.value=d[i].ssid;
        opt.textContent=d[i].ssid+' ('+d[i].rssi+'dBm)';
        sel.appendChild(opt);
      }}
    }}
    document.getElementById('wifi_list').style.display='block';
    document.getElementById('wifi_result').textContent='T\u00ECm th\u1EA5y '+d.length+' m\u1EA1ng';
  }}).catch(function(){{
    document.getElementById('wifi_result').textContent='L\u1ED7i qu\u00E9t WiFi';
  }});
}}
function connectWifi() {{
  var ssid=document.getElementById('wifi_ssid').value;
  var pwd=document.getElementById('wifi_password').value;
  if(!ssid){{document.getElementById('wifi_result').textContent='Ch\u01B0a ch\u1ECDn m\u1EA1ng';return;}}
  document.getElementById('wifi_result').textContent='\\u0110ang k\\u1ebft n\\u1ed1i...';
  fetch('/wifi/connect',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:'ssid='+encodeURIComponent(ssid)+'&password='+encodeURIComponent(pwd)}})
  .then(function(r){{return r.json()}}).then(function(d){{
    document.getElementById('wifi_result').textContent=d.success?'\u0110\u00E3 k\u1ebft n\u1ed1i!':'Th\u1EA5t b\u1EA1i: '+d.msg;
    upd();
  }}).catch(function(){{
    document.getElementById('wifi_result').textContent='L\u1ED7i k\u1ebft n\u1ed1i';
  }});
}}
function forgetWifi() {{
  fetch('/wifi/forget').then(function(r){{return r.json()}}).then(function(d){{
document.getElementById('wifi_result').textContent=d.ok?'\u0110\u00E3 x\u00F3a c\u1ea5u h\u00ECnh WiFi':'L\u1ED7i';
    upd();
  }});
}}
function scheduleUpd(){{setTimeout(function(){{try{{upd();}}catch(e){{}}scheduleUpd();}},1000);}}
function scheduleLogs(){{setTimeout(function(){{try{{updLogs();}}catch(e){{}}scheduleLogs();}},3000);}}
scheduleUpd();
scheduleLogs();
</script>
</body>
</html>"""

def build_html(cfg, cur_confirmed=None, cur_connected=True):
    opts = ''
    for p in ESP32_PINS:
        if p not in USED_PINS and p not in SERIAL_PINS and p not in INPUT_ONLY_PINS:
            sel = ' selected' if p == cfg['btn_pin'] else ''
            opts += f'<option value="{p}"{sel}>GPIO {p}</option>\n'
    sel_up = ' selected' if cfg.get('btn_mode', 'PULL_UP') == 'PULL_UP' else ''
    sel_down = ' selected' if cfg.get('btn_mode') == 'PULL_DOWN' else ''
    sel_momentary = ' selected' if cfg.get('btn_type', 'momentary') == 'momentary' else ''
    sel_toggle = ' selected' if cfg.get('btn_type') == 'toggle' else ''
    # Xác định trạng thái ban đầu dựa trên confirmed_value (hoặc last_value nếu chưa có confirmed)
    display_val = cur_confirmed if cur_confirmed is not None else cfg.get('last_value', cfg['value_off'])
    if display_val not in (cfg['value_on'], cfg['value_off']):
        display_val = cfg['value_off']

    led1_on = (display_val == cfg['value_on'])
    led1_init_class = 'on' if led1_on else 'off'
    led1_init_text = 'ON' if led1_on else 'OFF'
    led2_init_class = 'on' if not led1_on else 'off'
    led2_init_text = 'ON' if not led1_on else 'OFF'
    cur_val_init = str(display_val)

    if cur_confirmed is not None and cur_connected:
        if cur_confirmed == cfg['value_on']:
            big_init_text = 'ON'
            big_init_color = '#2ecc71'
            big_init_shadow = '0 0 15px #2ecc71'
            big_init_class = 'on'
        else:
            big_init_text = 'OFF'
            big_init_color = '#e74c3c'
            big_init_shadow = '0 0 15px #e74c3c'
            big_init_class = 'off'
    else:
        big_init_text = '--'
        big_init_color = '#555'
        big_init_shadow = 'none'
        big_init_class = 'disconnected'
    wcfg = wifi_load_config()
    wifi_ap_status = wcfg.get('ssid', '') or 'Chưa cấu hình WiFi'
    return HTML_PAGE.format(
        slave_id=cfg['slave_id'],
        reg_addr=cfg['reg_addr'],
        value_on=cfg['value_on'],
        value_off=cfg['value_off'],
        health_reg=cfg.get('health_reg', 36017),
        pin_options=opts,
        sel_up=sel_up,
        sel_down=sel_down,
        sel_momentary=sel_momentary,
        sel_toggle=sel_toggle,
        wifi_ap_status=wifi_ap_status,
        big_init_text=big_init_text,
        big_init_color=big_init_color,
        big_init_shadow=big_init_shadow,
        big_init_class=big_init_class,
        led1_init_class=led1_init_class,
        led1_init_text=led1_init_text,
        led2_init_class=led2_init_class,
        led2_init_text=led2_init_text,
        cur_val_init=cur_val_init
    )

def parse_post(data):
    """Trích xuất tham số từ POST body"""
    params = {}
    try:
        body = data.split(b'\r\n\r\n', 1)[1]
        for pair in body.decode().split('&'):
            if '=' in pair:
                k, v = pair.split('=', 1)
                params[k] = v.replace('+', ' ')
    except:
        pass
    return params

def handle_web():
    global config, btn, modbus_value, write_pending, desired_value, fail_count, modbus_connected, log_buffer, confirmed_value
    try:
        cl, addr = server_sock.accept()
        data = cl.recv(2048)
        if not data:
            cl.close()
            return

        line = data.split(b'\r\n')[0].decode()

        # === Endpoint /status: trả về JSON trạng thái LED + WiFi ===
        if 'GET /status' in line:
            state = 'ON' if modbus_value == config['value_on'] else 'OFF'
            if not modbus_connected:
                state = 'MẤT KẾT NỐI'
            wcfg = wifi_load_config()
            wifi_is_conn = sta.isconnected()
            status = json.dumps({
                'led1': led1.value(),
                'led2': led2.value(),
                'value': modbus_value,
                'val_on': config['value_on'],
                'val_off': config['value_off'],
                'state': state,
                'connected': modbus_connected,
                'wifi_connected': wifi_is_conn,
                'wifi_ssid': wcfg.get('ssid', ''),
                'wifi_ip': sta.ifconfig()[0] if wifi_is_conn else '',
                'wifi_saved': wcfg.get('ssid', '')
            })
            cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n' + status.encode())

        # === Endpoint /logs: trả về JSON danh sách log ===
        elif 'GET /logs' in line:
            cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n' + get_logs_json().encode())

        # === Endpoint /clearlogs: xóa log ===
        elif 'GET /clearlogs' in line:
            log_buffer = []
            cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"ok":true}')

        # === Endpoint /cmd: test lệnh ON/OFF ===
        elif 'GET /cmd' in line:
            val = 'off'
            if 'value=on' in line or 'value=ON' in line:
                val = 'on'
            if val == 'on':
                modbus_value = config['value_on']
            else:
                modbus_value = config['value_off']
            config['last_value'] = modbus_value
            save_config(config)
            add_log(f'Web test -> Slave {config["slave_id"]} Reg {config["reg_addr"]} = {modbus_value}')
            uart.read()
            write_modbus_register(config['slave_id'], config['reg_addr'], modbus_value)
            resp = wait_for_response(timeout_ms=500)
            success = verify_response(resp, config['slave_id'], config['reg_addr'], modbus_value)
            if success:
                fail_count = 0
                confirmed_value = modbus_value
                # Bật LED theo giá trị đã xác nhận thành công
                if confirmed_value == config['value_on']:
                    led1.value(1); led2.value(0)
                else:
                    led1.value(0); led2.value(1)
                if not modbus_connected:
                    modbus_connected = True
                    add_log('Modbus đã kết nối lại!')
            else:
                fail_count += 1
                if fail_count >= 200:
                    add_log('QUÁ 200 LẦN MẤT KẾT NỐI! RESET THIẾT BỊ...')
                    time.sleep_ms(500)
                    machine.reset()
                if fail_count >= 3 and modbus_connected:
                    modbus_connected = False
                    led1.value(0)
                    led2.value(0)
                    add_log('MẤT KẾT NỐI MODBUS!')
            msg = f'Ghi {modbus_value} thành công' if success else f'Ghi {modbus_value} không có phản hồi'
            result = json.dumps({'success': success, 'msg': msg})
            cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n' + result.encode())

        # === Endpoint /wifi/scan: quét mạng WiFi ===
        elif 'GET /wifi/scan' in line:
            nets = wifi_scan()
            cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n' + json.dumps(nets).encode())

        # === Endpoint /wifi/connect: kết nối WiFi ===
        elif 'POST /wifi/connect' in line or 'GET /wifi/connect' in line:
            params = parse_post(data) if b'POST' in data else {}
            if 'GET /wifi/connect' in line:
                # GET params from query string
                qs = line.split(' ')[1]
                if '?' in qs:
                    for pair in qs.split('?',1)[1].split('&'):
                        if '=' in pair:
                            k, v = pair.split('=',1)
                            params[k] = v.replace('+', ' ')
            ssid = params.get('ssid', '')
            password = params.get('password', '')
            # Giải mã URL-encoded thủ công
            def url_decode(s):
                s = s.replace('+', ' ')
                out = bytearray()
                i = 0
                while i < len(s):
                    if s[i] == '%' and i + 2 < len(s):
                        out.append(int(s[i+1:i+3], 16))
                        i += 3
                    else:
                        out.append(ord(s[i]))
                        i += 1
                try:
                    return out.decode('utf-8')
                except:
                    return out.decode()
            ssid = url_decode(ssid)
            password = url_decode(password)
            success = wifi_connect(ssid, password)
            msg = f'Đã kết nối {ssid}' if success else f'Không thể kết nối {ssid}'
            if success:
                add_log(msg)
            cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n' + json.dumps({'success': success, 'msg': msg}).encode())

        # === Endpoint /wifi/forget: xóa cấu hình WiFi ===
        elif 'GET /wifi/forget' in line or 'POST /wifi/forget' in line:
            wifi_disconnect()
            cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"ok":true}')

        # === Endpoint /save: lưu cấu hình ===
        elif 'POST' in line and '/save' in line:
            params = parse_post(data)
            changed = False
            if 'slave_id' in params:
                config['slave_id'] = int(params['slave_id'])
            if 'reg_addr' in params:
                config['reg_addr'] = int(params['reg_addr'])
            if 'value_on' in params:
                config['value_on'] = int(params['value_on'])
            if 'value_off' in params:
                config['value_off'] = int(params['value_off'])
            if 'health_reg' in params:
                config['health_reg'] = int(params['health_reg'])
            if 'btn_pin' in params:
                config['btn_pin'] = int(params['btn_pin'])
                changed = True
            if 'btn_mode' in params:
                config['btn_mode'] = params['btn_mode']
                changed = True
            if 'btn_type' in params:
                config['btn_type'] = params['btn_type']
                changed = True
            if changed:
                mode = PIN_MODES.get(config['btn_mode'], Pin.PULL_UP)
                btn = Pin(config['btn_pin'], Pin.IN, mode)
            save_config(config)
            add_log('Config saved: {}'.format(config))
            cl.send(b'HTTP/1.0 302 Redirect\r\nLocation: /\r\n\r\n')

        # === Mặc định: trả về trang HTML ===
        else:
            html = build_html(config, confirmed_value, modbus_connected)
            cl.send(html.encode())
        cl.close()
    except OSError:
        pass  # Timeout, không có kết nối đến

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

# ===================== MAIN LOOP =====================
# Khôi phục giá trị từ lần chạy trước
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
HEALTH_INTERVAL = 10000       # 10 giây
WRITE_TIMEOUT = 3000          # 3 giây timeout chờ ghi
NTP_INTERVAL = 86400000       # 24 giờ (ms) giữa các lần đồng bộ NTP
NTP_RETRY_INTERVAL = 300000   # 5 phút (ms) thử lại NTP khi thất bại

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

# ===================== AUTO-CONNECT WIFI + NTP =====================
wifi_cfg = wifi_load_config()
if wifi_cfg.get('ssid'):
    add_log(f'Đang tự động kết nối WiFi: {wifi_cfg["ssid"]}...')
    sta.active(True)
    sta.connect(wifi_cfg['ssid'], wifi_cfg['password'])
    timeout = 10000
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < timeout:
        if sta.isconnected():
            info = sta.ifconfig()
            add_log(f'WiFi đã kết nối! IP: {info[0]}')
            sync_ntp_time()
            break
        time.sleep_ms(500)
    else:
        add_log(f'Tự động kết nối WiFi thất bại (sẽ thử lại sau)')
else:
    add_log('Chưa có cấu hình WiFi, bỏ qua kết nối')

while True:
    # Feed watchdog: báo hiệu hệ thống còn sống.
    # Nếu main loop bị treo (không đến được đây trong WDT_TIMEOUT_MS),
    # ESP32 sẽ tự động reset.
    wdt.feed()

    now = time.ticks_ms()

    # 1. Xử lý web request (non-blocking)
    handle_web()

    # 2. Đọc nút nhấn (PULL_UP: nhấn=0; PULL_DOWN: nhấn=1)
    btn_state = btn.value()
    btn_type = config.get('btn_type', 'momentary')

    if btn_type == 'toggle':
        # Công tắc giữ trạng thái: đọc trực tiếp ON/OFF
        if config.get('btn_mode') == 'PULL_UP':
            switch_on = (btn_state == 0)   # PULL_UP: nhấn=0 (ON), nhả=1 (OFF)
        else:
            switch_on = (btn_state == 1)   # PULL_DOWN: nhấn=1 (ON), nhả=0 (OFF)
        new_value = config['value_on'] if switch_on else config['value_off']
        # Chỉ gửi lệnh Modbus khi giá trị thay đổi
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
        # Nút nhấn nhả (xung ngắn): phát hiện cạnh xuống
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
            time.sleep_ms(50)
    last_btn_state = btn_state

    # 3. Xử lý các lệnh Modbus đang chờ phản hồi
    if write_pending:
        # Kiểm tra timeout: nếu đã quá WRITE_TIMEOUT ms mà không có phản hồi,
        # thoát khỏi trạng thái chờ ghi để cho health check chạy
        if time.ticks_diff(now, last_write_time) >= WRITE_TIMEOUT:
            add_log('Timeout chờ ghi, chuyển sang health check...')
            write_pending = False
            # Không reset fail_count để vẫn theo dõi mất kết nối
            if fail_count >= 3 and modbus_connected:
                modbus_connected = False
                led1.value(0)
                led2.value(0)
                add_log('MẤT KẾT NỐI MODBUS!')
        else:
            resp = wait_for_response(timeout_ms=500)
            if verify_response(resp, config['slave_id'], config['reg_addr'], desired_value):
                add_log('Ghi thành công!')
                fail_count = 0
                confirmed_value = desired_value
                # Bật LED theo giá trị đã xác nhận thành công
                if confirmed_value == config['value_on']:
                    led1.value(1); led2.value(0)
                else:
                    led1.value(0); led2.value(1)
                if not modbus_connected:
                    modbus_connected = True
                    add_log('Modbus đã kết nối lại!')
                write_pending = False
                health_pending = False
                last_health_time = time.ticks_ms()  # Reset timer health check
            else:
                fail_count += 1
                add_log(f'Chưa có phản hồi ({fail_count}/10), sẽ thử lại...')
                if fail_count >= 200:
                    add_log('QUÁ 200 LẦN MẤT KẾT NỐI! RESET THIẾT BỊ...')
                    time.sleep_ms(500)
                    machine.reset()
                if fail_count >= 3 and modbus_connected:
                    modbus_connected = False
                    led1.value(0)
                    led2.value(0)
                    add_log('MẤT KẾT NỐI MODBUS!')
                time.sleep_ms(200)

    elif health_pending:
        # Chờ phản hồi từ lệnh health check
        resp = wait_for_response(timeout_ms=500)
        if verify_read_response(resp, config['slave_id'], config.get('health_reg', 36017)):
            # Health check OK
            health_pending = False
            last_health_time = time.ticks_ms()
            fail_count = 0
            if not modbus_connected:
                modbus_connected = True
                # Khôi phục đèn theo giá trị đã xác nhận gần nhất
                if confirmed_value == config['value_on']:
                    led1.value(1); led2.value(0)
                elif confirmed_value == config['value_off']:
                    led1.value(0); led2.value(1)
                else:
                    led1.value(0); led2.value(0)
                add_log('Modbus đã kết nối lại!')
        else:
            # Health check thất bại
            health_pending = False
            fail_count += 1
            add_log(f'Health check thất bại ({fail_count}/10)')
            if fail_count >= 200:
                add_log('QUÁ 200 LẦN MẤT KẾT NỐI! RESET THIẾT BỊ...')
                time.sleep_ms(500)
                machine.reset()
            if fail_count >= 3 and modbus_connected:
                modbus_connected = False
                led1.value(0)
                led2.value(0)
                add_log('MẤT KẾT NỐI MODBUS!')

    else:
        # Không có lệnh nào đang chờ -> kiểm tra định kỳ
        # Cho phép health check chạy CẢ KHI mất kết nối (để phát hiện phục hồi)
        if time.ticks_diff(now, last_health_time) >= HEALTH_INTERVAL:
            # Nếu đang mất kết nối, log ở mức debug hơn
            read_modbus_register(config['slave_id'], config.get('health_reg', 36017))
            health_pending = True
            last_health_time = now

    # 5. Đồng bộ NTP định kỳ (nếu có WiFi)
    if sta.isconnected():
        if ntp_retry_count >= 5:
            # Đã thất bại quá 5 lần → dừng retry tự động, nhường tài nguyên
            if ntp_retry_count == 5:
                add_log('NTP thất bại nhiều lần. Hãy thử kết nối WiFi khác trên giao diện web!')
                ntp_retry_count = 6  # Chỉ cảnh báo 1 lần
        elif last_ntp_time == 0:
            # Chưa đồng bộ lần nào → thử sau 5 giây khởi động
            if time.ticks_diff(now, 0) >= 5000:
                sync_ntp_time()
        elif time.ticks_diff(now, last_ntp_time) >= NTP_INTERVAL:
            # Đã đến lúc đồng bộ lại hàng ngày
            add_log('Đến giờ đồng bộ NTP định kỳ...')
            sync_ntp_time()
        elif ntp_retry_count > 0 and time.ticks_diff(now, last_ntp_time) >= NTP_RETRY_INTERVAL:
            # Lần trước thất bại, thử lại sau 5 phút
            add_log('Thử đồng bộ NTP lại...')
            sync_ntp_time()

    # 4. Nhấp nháy LED theo trạng thái
    if modbus_connected:
        led.toggle()
        if modbus_value == config['value_on']:
            time.sleep(1)
        else:
            time.sleep(0.2)
    else:
        led.toggle()
        time.sleep(0.5)  # Nhấp nháy chậm khi mất kết nối