#!/usr/bin/env python3
"""Supervisor that keeps ATEX server + Cloudflare tunnel alive"""
import subprocess, time, os, signal, sys, re, logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.info

def start_server():
    p = subprocess.Popen(
        ['python3', '-u', 'api/server.py', '8420'],
        cwd='/home/z/my-project/token_exchange',
        stdout=open('/tmp/atex_server.log', 'a'),
        stderr=subprocess.STDOUT
    )
    log(f"Server started PID={p.pid}")
    return p

def start_tunnel():
    p = subprocess.Popen(
        ['/home/z/my-project/bin/cloudflared', 'tunnel', '--url', 'http://127.0.0.1:8420'],
        stdout=open('/tmp/cloudflared.log', 'a'),
        stderr=subprocess.STDOUT
    )
    log(f"Tunnel started PID={p.pid}")
    return p

def get_tunnel_url():
    try:
        with open('/tmp/cloudflared.log') as f:
            content = f.read()
        urls = re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com', content)
        return urls[-1] if urls else None
    except:
        return None

def wait_for_server(timeout=15):
    import urllib.request
    for i in range(timeout):
        try:
            r = urllib.request.urlopen('http://127.0.0.1:8420/api/v1/status', timeout=2)
            if r.status == 200:
                return True
        except:
            pass
        time.sleep(1)
    return False

# Write URL to file for other processes to read
URL_FILE = '/home/z/my-project/token_exchange/data/tunnel_url.txt'

if __name__ == '__main__':
    log("ATEX Supervisor starting...")
    
    server_proc = start_server()
    if not wait_for_server():
        log("Server failed to start!")
        sys.exit(1)
    log("Server is ready")
    
    time.sleep(2)
    tunnel_proc = start_tunnel()
    
    # Wait for tunnel URL
    time.sleep(10)
    url = get_tunnel_url()
    if url:
        log(f"Tunnel URL: {url}")
        with open(URL_FILE, 'w') as f:
            f.write(url)
    
    # Monitor loop
    while True:
        # Check server
        if server_proc.poll() is not None:
            log(f"Server died (exit={server_proc.returncode}), restarting...")
            server_proc = start_server()
            wait_for_server()
        
        # Check tunnel
        if tunnel_proc.poll() is not None:
            log(f"Tunnel died (exit={tunnel_proc.returncode}), restarting...")
            # Clear old log to get fresh URL
            open('/tmp/cloudflared.log', 'w').close()
            tunnel_proc = start_tunnel()
            time.sleep(10)
            url = get_tunnel_url()
            if url:
                log(f"New tunnel URL: {url}")
                with open(URL_FILE, 'w') as f:
                    f.write(url)
        
        time.sleep(30)
