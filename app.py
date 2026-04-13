#!/usr/bin/env python3
"""
MODIFIED VERSION - Free Tier Only Filter
Original: https://github.com/sakalilion/jajang
Changes: Added stake detection, only mine free tier quests
"""

from gevent import monkey
monkey.patch_all()
import gevent

import subprocess
import requests
import time
import re
import os
import json
from flask import Flask, render_template
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# ==========================================
# KONFIGURASI AI & RAILWAY VARIABLES
# ==========================================
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")
API_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
MODEL_AI = "accounts/fireworks/models/glm-5p1"
WALLET_KEY = os.getenv("WALLET_KEY")

# FREE TIER SETTINGS
MAX_STAKE = 0.1  # Hanya mine kalau stake <= 0.1 NARA atau stakeRequired = False

UPDATE_BALANCE_EVERY = 5

stats = {
    "balance": "0.0",
    "success": 0,
    "failed": 0,
    "skipped_high_stake": 0,
    "current_q": "Menghubungkan ke mesin bot...",
    "current_r": "-",
    "logs": []
}

def clean_ansi(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def add_log(msg, type="INFO"):
    t = time.strftime("%H:%M:%S")
    emojis = {"INFO": "⚪", "OK": "🟢", "ERROR": "🔴", "WARN": "🟡", "AI": "🤖", "BANK": "🏦", "SKIP": "⏭️", "FREE": "✅"}
    entry = f"[{t}] {emojis.get(type, 'ℹ️')} {msg}"
    
    stats["logs"].insert(0, entry)
    if len(stats["logs"]) > 40:
        stats["logs"].pop()
    socketio.emit('update', stats)

def setup_wallet():
    if not WALLET_KEY:
        add_log("ERROR FATAL: WALLET_KEY tidak ditemukan di Variables Railway!", "ERROR")
        return False
    
    config_path = os.path.expanduser("~/.config/nara")
    os.makedirs(config_path, exist_ok=True)
    with open(f"{config_path}/id.json", "w") as f:
        f.write(WALLET_KEY)
    add_log("Wallet ID.json berhasil dikonfigurasi dari Railway.", "OK")
    return True

def sync_blockchain_balance():
    try:
        res = subprocess.run(["npx", "naracli", "balance"], capture_output=True, text=True, timeout=20)
        output = clean_ansi(res.stdout + res.stderr)
        match = re.search(r"Balance:\s*([\d\.]+)", output)
        if match:
            stats["balance"] = match.group(1)
            add_log(f"Saldo Tersinkronisasi: {stats['balance']} NARA", "BANK")
            socketio.emit('update', stats)
    except:
        add_log("Gagal sinkronisasi saldo (Blockchain sibuk)", "WARN")

def get_quest_json():
    """Get quest data as JSON with stake info"""
    try:
        res = subprocess.run(["npx", "naracli", "quest", "get", "--json"],
                          capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            return json.loads(res.stdout)
    except:
        pass
    return None

def is_free_tier(quest_data):
    """Check if quest is free tier (stake = 0 or not required)"""
    if not quest_data:
        return False
    
    # Check if stake is required
    if not quest_data.get('stakeRequired', True):
        return True
    
    # Check stake amount
    stake_str = quest_data.get('stakeRequirement', '0')
    try:
        stake = float(stake_str)
        return stake <= MAX_STAKE
    except:
        return False

def ask_ai(question, is_mc, previous_attempts=None):
    if not FIREWORKS_API_KEY:
        add_log("ERROR: FIREWORKS_API_KEY tidak tersedia!", "ERROR")
        return None
    
    if is_mc:
        system_msg = "QUIZ MODE: MULTIPLE CHOICE. Output ONLY the single letter (A, B, C, or D)."
    else:
        system_msg = "QUIZ MODE: ESSAY. Output ONLY the specific word/term. NEVER output just a single letter."
    
    prompt_retry = f"\n\nNote: Do NOT use these wrong answers: {', '.join(previous_attempts)}" if previous_attempts else ""
    
    headers = {"Authorization": f"Bearer {FIREWORKS_API_KEY}", "Content-Type": "application/json"}
    
    payload = {
        "model": MODEL_AI,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"Question: {question}{prompt_retry}\n\nAnswer:"}
        ],
        "temperature": 0.3 if previous_attempts else 0.0
    }
    
    try:
        res = requests.post(API_URL, headers=headers, json=payload, timeout=15)
        ans = res.json()['choices'][0]['message']['content'].strip().split('\n')[0]
        final = re.sub(r"^(Answer|Result|Option):\s*", "", ans, flags=re.IGNORECASE).strip()
        final = re.sub(r"^[A-Z][\.\)\-\s]+", "", final).strip()
        
        if not is_mc and len(final) <= 1:
            return None
        return final if len(final) == 1 else final.title()
    except Exception as e:
        add_log(f"AI Error: {str(e)[:50]}", "ERROR")
        return None

def submit_answer(answer):
    if not answer:
        return "ERROR"
    add_log(f"Mengirim Jawaban: {answer}", "INFO")
    try:
        res = subprocess.run(["npx", "naracli", "quest", "answer", answer],
                          capture_output=True, text=True, timeout=60)
        out = clean_ansi(res.stdout + "\n" + res.stderr).strip()
        out_lower = out.lower()
        
        if any(w in out_lower for w in ["success", "reward", "congratulations", "submitted", "already"]):
            return "SUCCESS"
        
        if any(w in out_lower for w in ["wrong", "incorrect", "invalid"]):
            add_log(f"Jawaban Salah: {out[:60]}...", "WARN")
            return "WRONG"
            
        add_log(f"Gagal (System/RPC): {out[:60]}...", "WARN")
        return "ERROR"
        
    except Exception as e:
        add_log(f"Kesalahan Sistem: {str(e)[:50]}", "ERROR")
        return "ERROR"

def bot_engine():
    gevent.sleep(3)
    add_log("Memulai Mesin Pemantau Kuis V20.0 (Free Tier Only)...", "AI")
    
    if not setup_wallet():
        return
    sync_blockchain_balance()
    
    stats["current_q"] = "Siaga! Memantau ronde kuis baru... (Free Tier Only)"
    socketio.emit('update', stats)
    
    last_r = None
    
    while True:
        try:
            # Polling setiap 500ms (super cepat)
            gevent.sleep(0.5)
            
            # Get quest data as JSON
            quest_data = get_quest_json()
            if not quest_data:
                continue
            
            curr_r = quest_data.get('round')
            q_text = quest_data.get('question', '')
            stake_req = quest_data.get('stakeRequirement', '0')
            stake_required = quest_data.get('stakeRequired', True)
            remaining_slots = quest_data.get('remainingRewardSlots', 0)
            
            # Skip jika round sama atau slots penuh
            if curr_r == last_r:
                continue
            if remaining_slots == 0:
                add_log(f"Round {curr_r}: Slots penuh, skip", "SKIP")
                last_r = curr_r
                continue
            
            # CHECK: Free tier only
            if not is_free_tier(quest_data):
                add_log(f"Round {curr_r}: HIGH STAKE ({stake_req} NARA) - Skip", "SKIP")
                stats["skipped_high_stake"] += 1
                last_r = curr_r
                continue
            
            # FREE TIER DETECTED!
            add_log(f"🔥 Round {curr_r}: FREE TIER! Stake: {stake_req} NARA", "FREE")
            add_log(f"Question: {q_text[:60]}...", "INFO")
            
            stats["current_r"] = curr_r
            stats["current_q"] = q_text
            socketio.emit('update', stats)
            
            # Process quest
            is_mc = bool(re.search(r"\b[A-D][\.\)]\s", q_text))
            history = []
            success = False
            max_tries = 4 if not is_mc else 3
            
            # AI attempts
            for i in range(max_tries):
                ans = ask_ai(q_text, is_mc, previous_attempts=history)
                if not ans:
                    add_log(f"AI attempt {i+1}/{max_tries}: No response", "WARN")
                    gevent.sleep(1)
                    continue
                
                add_log(f"AI jawab: \"{ans}\"", "AI")
                
                res_sub = submit_answer(ans)
                if res_sub == "SUCCESS":
                    stats["success"] += 1
                    add_log(f"✅ SUKSES! +{quest_data.get('rewardPerWinner', '0')} NARA", "OK")
                    
                    if stats["success"] % UPDATE_BALANCE_EVERY == 0:
                        sync_blockchain_balance()
                    
                    success = True
                    break
                elif res_sub == "WRONG":
                    history.append(ans)
                    gevent.sleep(2)
                else:
                    # ERROR (System/RPC) - Jangan masukkan ke history agar bisa dicoba lagi
                    gevent.sleep(2)
            

            
            if not success:
                stats["failed"] += 1
                add_log(f"❌ Gagal mine round {curr_r}", "ERROR")
            
            last_r = curr_r
            stats["current_q"] = "Siaga! Memantau ronde selanjutnya..."
            socketio.emit('update', stats)
            
        except Exception as e:
            add_log(f"Error: {str(e)[:80]}", "ERROR")
            gevent.sleep(2)

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print("Client Terhubung! Sinkronisasi UI...")
    socketio.emit('update', stats)

if __name__ == '__main__':
    socketio.start_background_task(bot_engine)
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
