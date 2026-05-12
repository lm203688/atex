#!/bin/bash
# Start ATEX API server
cd /home/z/my-project/token_exchange
python3 -u api/server.py 8420 &
SERVER_PID=$!
echo "ATEX Server PID: $SERVER_PID" >&2

# Wait for server to be ready
for i in $(seq 1 10); do
    if curl -s http://127.0.0.1:8420/api/v1/status > /dev/null 2>&1; then
        echo "Server ready!" >&2
        break
    fi
    sleep 1
done

# Start cloudflared tunnel
/home/z/my-project/bin/cloudflared tunnel --url http://127.0.0.1:8420 &
CF_PID=$!
echo "Cloudflared PID: $CF_PID" >&2

# Wait for either to exit
wait -n $SERVER_PID $CF_PID 2>/dev/null
echo "One process exited, stopping both..." >&2
kill $SERVER_PID $CF_PID 2>/dev/null
