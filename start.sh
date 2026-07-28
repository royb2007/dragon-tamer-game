#!/bin/bash
pkill -f 'python.*server\.py' 2>/dev/null
sleep 0.3
export PORT=5000
exec python server.py
