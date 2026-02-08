import { initScene, animate, updateStrategiesInScene } from './scene.js';
import { Api } from './api.js';

// Initialize architecture
const api = new Api();

document.addEventListener('DOMContentLoaded', async () => {
    console.log("System Initializing...");

    // Start 3D Scene
    initScene();
    animate();

    // Remove loading overlay after a delay
    setTimeout(() => {
        const loader = document.getElementById('loading-overlay');
        if (loader) loader.style.display = 'none';
        console.log("Visual Systems Online.");
    }, 1500);

    // Initial Data Fetch
    updateStatus();

    // Poll for updates (every 2s)
    setInterval(updateStatus, 2000);

    // Handle Chat
    const chatForm = document.getElementById('chat-form');
    if (chatForm) {
        chatForm.addEventListener('submit', handleChatSubmit);
    }
});

async function updateStatus() {
    try {
        const status = await api.getStatus();

        // Update Tick
        const tickEl = document.getElementById('tick-counter');
        if (tickEl) tickEl.innerText = `Tick #${status.tick_count}`;

        // Update P&L
        const pnlEl = document.getElementById('stat-pnl');
        if (pnlEl) {
            pnlEl.innerText = `$${status.today_pnl.toFixed(2)}`;
            pnlEl.classList.toggle('text-green-400', status.today_pnl >= 0);
            pnlEl.classList.toggle('text-red-400', status.today_pnl < 0);
        }

        // Update Strategies List
        const strategies = await api.getStrategies();
        renderStrategies(strategies);

        // Update 3D Scene
        updateStrategiesInScene(strategies);

    } catch (e) {
        console.warn("Connection lost...", e);
    }
}

function renderStrategies(strategies) {
    const list = document.getElementById('strategy-list');
    if (!list) return;

    list.innerHTML = strategies.map(s => `
        <div class="p-2 border-b border-cyan-500/10 hover:bg-cyan-500/5 transition-colors flex justify-between items-center group">
            <div>
                <div class="font-bold text-cyan-300 group-hover:text-cyan-100">${s.name}</div>
                <div class="text-xs text-cyan-600">${s.description}</div>
            </div>
            <div class="text-right">
                <div class="text-xs font-mono ${s.enabled ? 'text-green-400' : 'text-gray-500'}">
                    ${s.enabled ? 'ACTIVE' : 'OFFLINE'}
                </div>
                <div class="text-[10px] text-cyan-700">${s.risk_level.toUpperCase()}</div>
            </div>
        </div>
    `).join('');
}

async function handleChatSubmit(e) {
    e.preventDefault();
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;

    // Add user message to UI
    addChatMessage('USER', msg, 'user');
    input.value = '';

    // Send to backend
    try {
        // Echo "Thinking..."
        const thinkingId = addChatMessage('SYSTEM', 'Thinking...', 'system', true);

        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg })
        });

        const data = await response.json();

        removeChatMessage(thinkingId);
        addChatMessage('SYSTEM', data.response, 'system');

    } catch (e) {
        addChatMessage('SYSTEM', 'Error communicating with core.', 'error');
    }
}

function addChatMessage(sender, text, type = 'system', isTemporary = false) {
    const history = document.getElementById('chat-history');
    const id = 'msg-' + Date.now();
    const isUser = type === 'user';
    const alignClass = isUser ? 'ml-auto border-r-2 rounded-l-lg rounded-br-lg bg-cyan-600/20 text-right' : 'mr-auto border-l-2 rounded-r-lg rounded-bl-lg bg-cyan-900/20 text-left';
    const borderClass = isUser ? 'border-cyan-400' : 'border-cyan-500';

    const html = `
        <div id="${id}" class="chat-msg ${type} ${alignClass} ${borderClass} p-3 max-w-[80%] backdrop-blur-sm animate-fade-in-up">
            <div class="text-[10px] text-cyan-500 font-bold mb-1 tracking-widest opacity-70">${sender}</div>
            <div class="text-cyan-100">${text}</div>
        </div>
    `;
    history.insertAdjacentHTML('beforeend', html);
    history.scrollTop = history.scrollHeight;
    return id;
}

function removeChatMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}
