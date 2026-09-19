import requests, time
from stem import Signal
from stem.control import Controller

api_key = input("Moltbook API key: ").strip()
url = "https://www.moltbook.com/api/v1/agents/me/identity-token"
proxies = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}

def new_circuit():
    try:
        with Controller.from_port(port=9051) as c:
            c.authenticate()
            c.signal(Signal.NEWNYM)
        time.sleep(3)
    except Exception as e:
        print("NEWNYM err:", e)

def get_exit_ip():
    try:
        return requests.get("https://api.ipify.org", proxies=proxies, timeout=15).text.strip()
    except Exception as e:
        return f"ERR:{e}"

def get_country(ip):
    try:
        return requests.get(f"http://ip-api.com/json/{ip}",
                            proxies=proxies, timeout=10).json().get("countryCode", "??")
    except Exception:
        return "??"

seen = set()
found = None
for i in range(1, 16):
    ip = get_exit_ip()
    cc = get_country(ip)
    try:
        r = requests.post(url, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        }, proxies=proxies, timeout=30)
        tag = "OK" if r.status_code == 201 else ("CF" if "cloudfront" in r.text.lower() else f"HTTP{r.status_code}")
        dup = "" if ip not in seen else " (dup)"
        print(f"[{i:2}] {ip:16} {cc:3}  {tag}{dup}")
        seen.add(ip)
        if r.status_code == 201:
            found = ip
            print(f"\n✅ рабочий exit: {ip} ({cc})")
            break
    except Exception as e:
        print(f"[{i:2}] {ip:16} {cc:3}  ERR {e}")
    new_circuit()

if not found:
    print("\n❌ ни один exit не прошёл — все в блок-листе CloudFront")
