"""main.py - Solis EPM MODE: GPIO18=0 (WiFi), GPIO18=1 (BLE)"""
import sys,time,json,network,socket,machine,gc
from machine import Pin,UART,WDT

# ===== CHON MODE =====
mode_pin=Pin(18,Pin.IN,Pin.PULL_DOWN)
time.sleep_ms(100)
MODE=mode_pin.value()
print(f'MODE={"BLE" if MODE else "WiFi"} GPIO18={MODE}')

# ===== CAU HINH =====
DEFAULT={"slave_id":101,"reg_addr":36508,"value_on":4400,"value_off":0,
         "btn_pin":5,"btn_mode":"PULL_UP","btn_type":"momentary","health_reg":36017,"last_value":0}
CFG='modbus_config.json'; WIFI_CFG='wifi_config.json'
PIN_M={'PULL_UP':Pin.PULL_UP,'PULL_DOWN':Pin.PULL_DOWN}

def load_cfg():
    try:
        with open(CFG) as f:
            c=json.load(f)
            for k,v in DEFAULT.items(): c.setdefault(k,v)
            return c
    except: return dict(DEFAULT)
def save_cfg(c):
    with open(CFG,'w') as f: json.dump(c,f)
def wifi_load():
    try:
        with open(WIFI_CFG) as f: return json.load(f)
    except: return {"ssid":"","password":""}
def wifi_save(s,p):
    with open(WIFI_CFG,'w') as f: json.dump({"ssid":s,"password":p},f)

cfg=load_cfg()
modbus_val=cfg.get('last_value',cfg['value_off'])
if modbus_val not in (cfg['value_on'],cfg['value_off']):
    modbus_val=cfg['value_off']

# ===== PHAN CUNG =====
uart=UART(1,baudrate=9600,bits=8,parity=None,stop=1,tx=17,rx=16)
led=Pin(2,Pin.OUT); led1=Pin(15,Pin.OUT); led2=Pin(4,Pin.OUT)
wdt=WDT(timeout=10000)
btn=Pin(cfg['btn_pin'],Pin.IN,PIN_M.get(cfg['btn_mode'],Pin.PULL_UP))

print(f'Modbus: Slave {cfg["slave_id"]} Reg {cfg["reg_addr"]} ON={cfg["value_on"]} OFF={cfg["value_off"]}')

# ===== MODBUS =====
def crc16(d):
    c=0xFFFF
    for b in d:
        c^=b
        for _ in range(8): c=(c>>1)^0xA001 if c&1 else c>>1
    return c.to_bytes(2,'little')

def wr_mb(sid,addr,val):
    f=bytearray([sid,6,(addr>>8)&0xFF,addr&0xFF,(val>>8)&0xFF,val&0xFF])
    f.extend(crc16(f)); uart.write(f)

def rd_mb(sid,addr):
    f=bytearray([sid,4,(addr>>8)&0xFF,addr&0xFF,0,1])
    f.extend(crc16(f)); uart.read(); uart.write(f)

def wait_rsp(t=500):
    b=bytearray(); s=time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(),s)<t:
        if uart.any(): b.extend(uart.read())
        if len(b)>=5: time.sleep_ms(30)
        if not uart.any() and len(b)>=5: return b
        time.sleep_ms(10)
    return b if b else None

def vrf_wr(f,sid,addr,val):
    if not f or len(f)<8: return False
    c=crc16(f[:6])
    return f[6]==c[0] and f[7]==c[1] and f[0]==sid and f[1]==6 and \
           f[2]==(addr>>8)&0xFF and f[3]==addr&0xFF and \
           f[4]==(val>>8)&0xFF and f[5]==val&0xFF

def vrf_rd(f,sid,addr):
    if not f or len(f)<5: return False
    if f[0]!=sid or f[1]!=4: return False
    c=crc16(f[:len(f)-2])
    return f[-2]==c[0] and f[-1]==c[1]

# ===================================================================
# MODE 0: WiFi AP + Modbus
# ===================================================================
if MODE==0:
    # WiFi AP
    ap=network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid='SolisEPM-Control',password='19001006',authmode=network.AUTH_WPA_WPA2_PSK,channel=6,hidden=0,max_clients=4)
    ap.ifconfig(('192.168.88.1','255.255.255.0','192.168.88.1','8.8.8.8'))
    print('AP: SolisEPM-Control | 192.168.88.1')
    sta=network.WLAN(network.STA_IF)
    sta.active(True)

    def wifi_scan():
        try:
            n=sta.scan(); r=[]; s=set()
            for x in n:
                ssid=x[0].decode()
                if ssid and ssid not in s: s.add(ssid); r.append({'ssid':ssid,'rssi':x[3]})
            r.sort(key=lambda x:x['rssi'],reverse=True)
            return r
        except: return []

    def wifi_con(ssid,pw):
        if not ssid: return False
        if sta.isconnected(): sta.disconnect(); time.sleep_ms(500)
        sta.connect(ssid,pw)
        for _ in range(15):
            if sta.isconnected(): wifi_save(ssid,pw); return True
            time.sleep_ms(1000)
        return False

    def wifi_dis():
        sta.disconnect(); wifi_save("","")

    # HTML sieu nhe, day du tinh nang
    HTML="""HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n<!DOCTYPE html><html><head><meta charset=UTF-8><meta name=viewport content="width=device-width,initial-scale=1"><title>SolisEPM</title><style>*{margin:0;padding:0;font-family:Arial,sans-serif}body{background:#1a1a2e;color:#eee;padding:16px;max-width:480px;margin:auto}h1{color:#e94560;text-align:center;font-size:20px;margin-bottom:12px}.c{background:#16213e;border-radius:8px;padding:12px;margin-bottom:10px}.c h2{font-size:14px;color:#e94560;margin-bottom:8px}.l{display:flex;align-items:center;gap:8px;font-size:14px;margin:4px 0}.d{width:12px;height:12px;border-radius:50%;display:inline-block}.d1{background:#2ecc71;box-shadow:0 0 8px #2ecc71}.d0{background:#555}.bg{border:3px solid #e94560;border-radius:10px;text-align:center;padding:10px;background:#0f3460;margin:6px 0}.bg .v{font-size:28px;font-weight:bold}.r{display:flex;gap:8px}.btn{flex:1;padding:10px;border:none;border-radius:6px;cursor:pointer;font-weight:bold;font-size:14px;color:#fff;margin:4px 0}.b2{background:#27ae60}.b3{background:#e74c3c}.b4{background:#3498db}.lb{font-size:13px;color:#aaa;margin:6px 0 2px;display:block}.in{width:100%;padding:8px;border:none;border-radius:5px;background:#0f3460;color:#fff;font-size:14px;outline:none;margin:2px 0}.s{text-align:center;font-size:12px;color:#666;margin-top:8px}</style></head><body><h1>Solis EPM</h1><div class=c><h2>Trang thai</h2><div class=l><span class=d id=d1></span>LED ON: <b id=t1>--</b></div><div class=l><span class=d id=d2></span>LED OFF: <b id=t2>--</b></div><div class=bg><div class=v id=sv>--</div></div><div id=cs style="font-size:12px;padding:4px;border-radius:4px;text-align:center;margin:4px 0"></div></div><div class=c><h2>Dieu khien</h2><div class=r><button class="btn b2" onclick="c(1)">BAT</button><button class="btn b3" onclick="c(0)">TAT</button></div><div id=tr style="font-size:13px;color:#aaa;text-align:center;min-height:18px"></div></div><div class=c><h2>WiFi</h2><div id=ws style="font-size:12px;padding:4px;border-radius:4px;text-align:center;margin-bottom:6px"></div><button class="btn b4" onclick=wq()>Quet</button><div id=wl style="display:none;margin-top:6px"><select id=wsid class=in></select><input class=in id=wpw placeholder="Mat khau"><div class=r><button class="btn b2" onclick=wc()>Ket noi</button><button class="btn b3" onclick=wf()>Quen</button></div><div id=wr style="font-size:12px;color:#aaa;text-align:center;min-height:18px"></div></div></div><div class=c><h2>Cau hinh</h2><label class=lb>Slave ID</label><input class=in id=si value=101><label class=lb>Reg Address</label><input class=in id=ra value=36508><label class=lb>Value ON</label><input class=in id=vo value=4400><label class=lb>Value OFF</label><input class=in id=vf value=0><label class=lb>Health Reg</label><input class=in id=hr value=36017><button class="btn b2" onclick=svcf()>Luu</button><div id=sr style="font-size:12px;color:#aaa;text-align:center;min-height:18px;margin-top:4px"></div></div><div class=s>192.168.88.1 | WiFi Mode</div><script>var gp=function(u){return fetch(u).then(function(r){return r.json()})};function up(){gp('/s').then(function(d){document.getElementById('d1').className='d '+(d.l1?'d1':'d0');document.getElementById('d2').className='d '+(d.l2?'d1':'d0');document.getElementById('t1').textContent=d.l1?'ON':'OFF';document.getElementById('t2').textContent=d.l2?'ON':'OFF';var v=document.getElementById('sv');if(d.c&&d.v==d.vo){v.textContent='ON';v.style.color='#2ecc71'}else if(d.c&&d.v==d.vf){v.textContent='OFF';v.style.color='#e74c3c'}else{v.textContent='--';v.style.color='#555'}var cs=document.getElementById('cs');cs.innerHTML=d.c?'Modbus OK':'MAT KET NOI';cs.style.background=d.c?'#1e824c':'#c0392b';var w=document.getElementById('ws');if(d.wi){w.innerHTML='WiFi: '+d.ws;w.style.background='#1e824c'}else{w.innerHTML='AP mode';w.style.background='#555'}})};function c(v){document.getElementById('tr').textContent='...';gp('/c?v='+v).then(function(d){document.getElementById('tr').textContent=d.ok?'OK':'Loi';up()})};function wq(){gp('/w').then(function(d){var s=document.getElementById('wsid');s.innerHTML='';if(!d||!d.length){s.innerHTML='<option>Khong co</option>'}else{d.forEach(function(n){var o=document.createElement('option');o.value=n.ssid;o.textContent=n.ssid;o.textContent+=' ('+n.rssi+'dBm)';s.appendChild(o)})};document.getElementById('wl').style.display='block';document.getElementById('wr').textContent='Tim thay '+(d?d.length:0)+' mang'})};function wc(){var s=document.getElementById('wsid').value,p=document.getElementById('wpw').value;if(!s)return;document.getElementById('wr').textContent='...';gp('/wc?s='+encodeURIComponent(s)+'&p='+encodeURIComponent(p)).then(function(d){document.getElementById('wr').textContent=d.ok?'OK!':'That bai';up()})};function wf(){gp('/wf').then(function(){document.getElementById('wr').textContent='Da xoa';document.getElementById('wl').style.display='none';up()})};function svcf(){gp('/sv?slave_id='+document.getElementById('si').value+'&reg_addr='+document.getElementById('ra').value+'&value_on='+document.getElementById('vo').value+'&value_off='+document.getElementById('vf').value+'&health_reg='+(document.getElementById('hr')?document.getElementById('hr').value:'36017')).then(function(d){document.getElementById('sr').textContent=d.ok?'Da luu!':'Loi';up()})};setInterval(up,2000);up();</script></body></html>"""

    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.bind(('0.0.0.0',80)); sock.listen(1); sock.settimeout(0.3)
    print('Web: port 80')

    # Khoi tao bien
    cv=None; wp=False; dv=0; fc=0; mc=True; hp=False; lh=0; lw=0; lbs=0; ll=0
    HI=10000; WT=3000; led1.value(0); led2.value(0)

    # Gui lai gia tri
    uart.read(); wr_mb(cfg['slave_id'],cfg['reg_addr'],modbus_val)
    dv=modbus_val; wp=True; lw=time.ticks_ms(); lh=time.ticks_ms()
    r=wait_rsp(1000)
    if vrf_wr(r,cfg['slave_id'],cfg['reg_addr'],dv):
        cv=dv; led1.value(1 if cv==cfg['value_on'] else 0)
        led2.value(1 if cv==cfg['value_off'] else 0)

    while True:
        wdt.feed()
        if mode_pin.value()!=MODE: time.sleep_ms(500); machine.reset()
        now=time.ticks_ms()
        # Web
        try:
            cl,a=sock.accept(); d=cl.recv(2048)
            if d:
                l=d.split(b'\r\n')[0].decode()
                if 'GET /s' in l:
                    r=json.dumps({'l1':led1.value(),'l2':led2.value(),'v':modbus_val,'vo':cfg['value_on'],'vf':cfg['value_off'],'c':mc,'wi':sta.isconnected(),'ws':wifi_load().get('ssid','')})
                    cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n'+r.encode())
                elif 'GET /c' in l:
                    modbus_val=cfg['value_on'] if 'v=1' in l else cfg['value_off']
                    cfg['last_value']=modbus_val; save_cfg(cfg)
                    uart.read(); wr_mb(cfg['slave_id'],cfg['reg_addr'],modbus_val)
                    dv=modbus_val; wp=True; lw=now
                    cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"ok":true}')
                elif 'GET /w' in l:
                    cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n'+json.dumps(wifi_scan()).encode())
                elif 'GET /wc' in l:
                    qs=l.split(' ')[1].split('?',1)[1] if '?' in l.split(' ')[1] else ''; p={}
                    for x in qs.split('&'):
                        if '=' in x: k,v=x.split('=',1); p[k]=v.replace('+',' ')
                    ok=wifi_con(p.get('s',''),p.get('p',''))
                    cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n'+json.dumps({'ok':ok}).encode())
                elif 'GET /wf' in l:
                    wifi_dis(); cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"ok":true}')
                elif 'GET /sv' in l:
                    try:
                        qs=l.split(' ')[1].split('?',1)[1] if '?' in l.split(' ')[1] else ''; p={}
                        for x in qs.split('&'):
                            if '=' in x: k,v=x.split('=',1); p[k]=v
                        if 'slave_id' in p: cfg['slave_id']=int(p['slave_id'])
                        if 'reg_addr' in p: cfg['reg_addr']=int(p['reg_addr'])
                        if 'value_on' in p: cfg['value_on']=int(p['value_on'])
                        if 'value_off' in p: cfg['value_off']=int(p['value_off'])
                        if 'health_reg' in p: cfg['health_reg']=int(p['health_reg'])
                        if 'btn_pin' in p: cfg['btn_pin']=int(p['btn_pin'])
                        if 'btn_mode' in p: cfg['btn_mode']=p['btn_mode']
                        if 'btn_type' in p: cfg['btn_type']=p['btn_type']
                        save_cfg(cfg)
                        cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"ok":true}')
                    except Exception as e:
                        cl.send(b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"ok":false}')
                else: cl.send(HTML.encode())
            cl.close()
        except: pass

        # Nut bam
        bv=btn.value(); bt=cfg.get('btn_type','momentary')
        if bt=='toggle':
            sw=(bv==0) if cfg.get('btn_mode')=='PULL_UP' else (bv==1)
            nv=cfg['value_on'] if sw else cfg['value_off']
            if nv!=modbus_val and not wp:
                modbus_val=nv; cfg['last_value']=nv; save_cfg(cfg)
                uart.read(); wr_mb(cfg['slave_id'],cfg['reg_addr'],modbus_val)
                dv=nv; wp=True; lw=now
        else:
            press=(bv==0 and lbs==1) if cfg.get('btn_mode')=='PULL_UP' else (bv==1 and lbs==0)
            if press and not wp:
                modbus_val=cfg['value_off'] if modbus_val==cfg['value_on'] else cfg['value_on']
                cfg['last_value']=modbus_val; save_cfg(cfg)
                uart.read(); wr_mb(cfg['slave_id'],cfg['reg_addr'],modbus_val)
                dv=modbus_val; wp=True; lw=now
        lbs=bv

        # Modbus xu ly
        if wp:
            if time.ticks_diff(now,lw)>=WT:
                wp=False
                if fc>=3 and mc: mc=False; led1.value(0); led2.value(0)
            else:
                r=wait_rsp(300)
                if vrf_wr(r,cfg['slave_id'],cfg['reg_addr'],dv):
                    fc=0; cv=dv; led1.value(1 if cv==cfg['value_on'] else 0)
                    led2.value(1 if cv==cfg['value_off'] else 0)
                    mc=True; wp=False; hp=False; lh=now
                else:
                    fc+=1
                    if fc>=200: time.sleep_ms(500); machine.reset()
                    if fc>=3 and mc: mc=False; led1.value(0); led2.value(0)
        elif hp:
            r=wait_rsp(300)
            if vrf_rd(r,cfg['slave_id'],cfg.get('health_reg',36017)):
                hp=False; lh=now; fc=0
                if not mc: mc=True; led1.value(1 if cv==cfg['value_on'] else 0); led2.value(1 if cv==cfg['value_off'] else 0)
            else:
                hp=False; fc+=1
                if fc>=200: time.sleep_ms(500); machine.reset()
                if fc>=3 and mc: mc=False; led1.value(0); led2.value(0)
        else:
            if time.ticks_diff(now,lh)>=HI:
                rd_mb(cfg['slave_id'],cfg.get('health_reg',36017)); hp=True; lh=now

        # LED blink
        cv2=cv if cv is not None else modbus_val
        if not mc: li=500
        elif cv2==cfg['value_on']: li=1000
        else: li=200
        if time.ticks_diff(now,ll)>=li: led.value(not led.value()); ll=now

# ===================================================================
# MODE 1: BLE + Modbus
# ===================================================================
else:
    import uasyncio as asyncio,bluetooth,aioble
    _NUS=bluetooth.UUID("6e400001-b5a3-f393-e0a9-e50e24dcca9e")
    _RX=bluetooth.UUID("6e400002-b5a3-f393-e0a9-e50e24dcca9e")
    _TX=bluetooth.UUID("6e400003-b5a3-f393-e0a9-e50e24dcca9e")
    svc=aioble.Service(_NUS)
    ble_rx=aioble.Characteristic(svc,_RX,write=True,capture=True,initial=b'\x00'*256)
    ble_tx=aioble.Characteristic(svc,_TX,read=True,notify=True,initial=b'\x00'*256)
    aioble.register_services(svc)
    print('BLE OK')

    cv=None; wp=False; dv=0; fc=0; mc=True; hp=False; lh=0; lw=0; lbs=0; ll=0
    HI=10000; WT=3000; ble_buf=''; led1.value(0); led2.value(0)

    def ble_send(txt):
        try:
            raw=(txt+'\n').encode()
            for i in range(0,len(raw),20):
                ble_tx.write(raw[i:i+20],send_update=True)
                time.sleep_ms(15)
        except: pass

    def ble_sync():
        ble_send(json.dumps({"type":"sync","slave_id":cfg['slave_id'],"reg_addr":cfg['reg_addr'],"health_reg":cfg.get('health_reg',36017),"value_on":cfg['value_on'],"value_off":cfg['value_off'],"cur_val":modbus_val,"connected":mc,"btn_pin":cfg['btn_pin'],"btn_mode":cfg.get('btn_mode','PULL_UP'),"btn_type":cfg.get('btn_type','momentary')}))

    def do_cmd(val):
        global modbus_val,wp,dv,lw
        modbus_val=cfg['value_on'] if val else cfg['value_off']
        cfg['last_value']=modbus_val; save_cfg(cfg)
        uart.read(); wr_mb(cfg['slave_id'],cfg['reg_addr'],modbus_val)
        dv=modbus_val; wp=True; lw=time.ticks_ms()

    async def ble_adv():
        while True:
            try:
                gc.collect()
                async with await aioble.advertise(500000,name='SolisEPM') as conn:
                    print('BLE connected')
                    await asyncio.sleep_ms(500)
                    ble_sync()
                    await conn.disconnected()
                    print('BLE disconnected')
            except: await asyncio.sleep_ms(2000)

    async def ble_rx_task():
        global ble_buf,cfg,btn,modbus_val,wp,dv,lw
        while True:
            try:
                c,d=await ble_rx.written()
                if not d: continue
                ble_buf+=d.decode()
                if '\n' in ble_buf:
                    ls=ble_buf.split('\n'); ble_buf=ls.pop()
                    for l in ls:
                        l=l.strip()
                        if not l: continue
                        if l=='on': do_cmd(1); ble_sync()
                        elif l=='off': do_cmd(0); ble_sync()
                        else:
                            try:
                                j=json.loads(l); t=j.get('type','')
                                if t=='sync': ble_sync()
                                elif t=='time' and 'epoch' in j:
                                    ep=int(j['epoch'])+7*3600
                                    t2=time.localtime(ep)
                                    machine.RTC().datetime((t2[0],t2[1],t2[2],t2[6],t2[3],t2[4],t2[5],0))
                                elif t=='save':
                                    for k in ('slave_id','reg_addr','health_reg','value_on','value_off','btn_pin'):
                                        if k in j: cfg[k]=int(j[k])
                                    if 'btn_mode' in j: cfg['btn_mode']=j['btn_mode']
                                    if 'btn_type' in j: cfg['btn_type']=j['btn_type']
                                    save_cfg(cfg)
                                    if 'btn_pin' in j: btn=Pin(cfg['btn_pin'],Pin.IN,PIN_M.get(cfg['btn_mode'],Pin.PULL_UP))
                                    ble_sync()
                            except: pass
            except: await asyncio.sleep_ms(500)

    async def main_loop():
        global cv,wp,dv,fc,mc,hp,lh,lw,lbs,ll,modbus_val
        uart.read(); wr_mb(cfg['slave_id'],cfg['reg_addr'],modbus_val)
        dv=modbus_val; wp=True; lw=time.ticks_ms(); lh=time.ticks_ms()
        while True:
            await asyncio.sleep_ms(30)
            wdt.feed()
            if mode_pin.value()!=MODE: time.sleep_ms(500); machine.reset()
            now=time.ticks_ms()
            bv=btn.value(); bt=cfg.get('btn_type','momentary')
            if bt=='toggle':
                sw=(bv==0) if cfg.get('btn_mode')=='PULL_UP' else (bv==1)
                nv=cfg['value_on'] if sw else cfg['value_off']
                if nv!=modbus_val and not wp:
                    modbus_val=nv; cfg['last_value']=nv; save_cfg(cfg)
                    uart.read(); wr_mb(cfg['slave_id'],cfg['reg_addr'],modbus_val)
                    dv=nv; wp=True; lw=now
            else:
                press=(bv==0 and lbs==1) if cfg.get('btn_mode')=='PULL_UP' else (bv==1 and lbs==0)
                if press and not wp:
                    modbus_val=cfg['value_off'] if modbus_val==cfg['value_on'] else cfg['value_on']
                    cfg['last_value']=modbus_val; save_cfg(cfg)
                    uart.read(); wr_mb(cfg['slave_id'],cfg['reg_addr'],modbus_val)
                    dv=modbus_val; wp=True; lw=now
            lbs=bv
            if wp:
                if time.ticks_diff(now,lw)>=WT:
                    wp=False
                    if fc>=3 and mc: mc=False; led1.value(0); led2.value(0)
                else:
                    r=wait_rsp(300)
                    if vrf_wr(r,cfg['slave_id'],cfg['reg_addr'],dv):
                        fc=0; cv=dv; led1.value(1 if cv==cfg['value_on'] else 0); led2.value(1 if cv==cfg['value_off'] else 0)
                        mc=True; wp=False; hp=False; lh=now
                    else:
                        fc+=1
                        if fc>=200: time.sleep_ms(500); machine.reset()
                        if fc>=3 and mc: mc=False; led1.value(0); led2.value(0)
            elif hp:
                r=wait_rsp(300)
                if vrf_rd(r,cfg['slave_id'],cfg.get('health_reg',36017)):
                    hp=False; lh=now; fc=0
                    if not mc: mc=True; led1.value(1 if cv==cfg['value_on'] else 0); led2.value(1 if cv==cfg['value_off'] else 0)
                else:
                    hp=False; fc+=1
                    if fc>=200: time.sleep_ms(500); machine.reset()
                    if fc>=3 and mc: mc=False; led1.value(0); led2.value(0)
            else:
                if time.ticks_diff(now,lh)>=HI:
                    rd_mb(cfg['slave_id'],cfg.get('health_reg',36017)); hp=True; lh=now
            cv2=cv if cv is not None else modbus_val
            li=1000 if cv2==cfg['value_on'] else (200 if cv2==cfg['value_off'] else 500)
            if time.ticks_diff(now,ll)>=li: led.value(not led.value()); ll=now

    async def main():
        gc.collect()
        print(f'RAM: {gc.mem_free()}')
        await asyncio.gather(main_loop(),ble_adv(),ble_rx_task())
    asyncio.run(main())
