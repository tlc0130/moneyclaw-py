import { initScene, animate, updateStrategiesInScene } from './scene.js';
import { Api } from './api.js';

// Initialize architecture
const api = new Api();

// Pending strategy confirmation state
let pendingStrategyConfirmation = null;

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

    // Load strategy templates for suggestions
    loadStrategyTemplates();
});

async function updateStatus() {
    try {
        const status = await api.getStatus();

        // Update Tick
        const tickEl = document.getElementById('tick-counter');
        if (tickEl) tickEl.innerText = `Tick #${status.tick_count}`;

        // Update Wallet Value
        const walletEl = document.getElementById('stat-wallet');
        const walletLabelEl = document.getElementById('stat-wallet-label');
        if (walletEl && walletLabelEl) {
            const walletValue = status.wallet_value || 0;
            const isDryRun = status.dry_run;
            const paperPortfolio = status.paper_portfolio;

            if (walletValue > 0 && !isDryRun) {
                walletEl.innerText = `$${walletValue.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                walletLabelEl.innerText = 'Live Trading';
                walletLabelEl.className = 'text-[9px] text-green-500';
                walletEl.className = 'text-2xl font-bold font-orbitron text-cyan-300';
            } else if (isDryRun && paperPortfolio && paperPortfolio.total_market_value > 0) {
                walletEl.innerText = `$${paperPortfolio.total_market_value.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                walletLabelEl.innerText = `Paper P&L ${paperPortfolio.total_pnl >= 0 ? '+' : ''}$${paperPortfolio.total_pnl.toFixed(2)}`;
                walletLabelEl.className = `text-[9px] ${paperPortfolio.total_pnl >= 0 ? 'text-green-500' : 'text-red-400'}`;
                walletEl.className = 'text-2xl font-bold font-orbitron text-yellow-400';
            } else if (isDryRun) {
                walletEl.innerText = 'DRY RUN';
                walletLabelEl.innerText = 'Simulation Mode';
                walletLabelEl.className = 'text-[9px] text-yellow-500';
                walletEl.className = 'text-xl font-bold font-orbitron text-yellow-500/80';
            } else {
                walletEl.innerText = '$0.00';
                walletLabelEl.innerText = 'Not connected';
                walletLabelEl.className = 'text-[9px] text-cyan-700';
                walletEl.className = 'text-2xl font-bold font-orbitron text-cyan-300';
            }
        }

        // Update P&L
        const pnlEl = document.getElementById('stat-pnl');
        if (pnlEl) {
            const paperPnl = status.paper_portfolio ? status.paper_portfolio.total_pnl : null;
            const pnlValue = paperPnl !== null ? paperPnl : status.today_pnl;
            pnlEl.innerText = `$${pnlValue.toFixed(2)}`;
            pnlEl.classList.toggle('text-green-400', pnlValue >= 0);
            pnlEl.classList.toggle('text-red-400', pnlValue < 0);
        }

        // Update Strategies List
        const strategies = await api.getStrategies();
        renderStrategies(strategies);

        // Update recent actions panel
        const history = await api.getRecentActions();
        renderRecentActions(history);

        // Update 3D Scene
        updateStrategiesInScene(strategies);

    } catch (e) {
        console.warn("Connection lost...", e);
    }
}

// Store strategies data for modal access
let currentStrategies = [];

function renderRecentActions(history) {
    const container = document.getElementById('recent-actions');
    if (!container) return;

    if (!history || history.length === 0) {
        container.innerHTML = '<div class="text-cyan-700 text-center mt-10">No dry-run actions yet.</div>';
        return;
    }

    container.innerHTML = history.slice(0, 10).map(item => {
        const ts = item.executed_at ? new Date(item.executed_at * 1000) : null;
        const timeText = ts ? ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '--:--';
        const pnl = Number(item.profit_loss || 0);
        const pnlClass = pnl > 0 ? 'text-green-400' : pnl < 0 ? 'text-red-400' : 'text-cyan-300';
        const title = item.title || item.strategy || 'Action';
        const strategy = item.strategy || 'unknown';
        const dryRun = item.details && item.details.dry_run ? 'DRY RUN' : 'EVENT';

        return `
            <div class="p-3 rounded border border-cyan-500/10 bg-cyan-950/20 hover:bg-cyan-900/30 transition-colors">
                <div class="flex justify-between items-start gap-3">
                    <div class="min-w-0 flex-1">
                        <div class="text-cyan-300 font-bold truncate">${title}</div>
                        <div class="text-[11px] text-cyan-600 uppercase tracking-wider mt-1">${strategy} • ${dryRun}</div>
                    </div>
                    <div class="text-right shrink-0">
                        <div class="text-xs font-mono ${pnlClass}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</div>
                        <div class="text-[10px] text-cyan-700 mt-1">${timeText}</div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function renderStrategies(strategies) {
    const list = document.getElementById('strategy-list');
    if (!list) return;

    currentStrategies = strategies;

    list.innerHTML = strategies.map((s, index) => `
        <div class="strategy-item p-3 border-b border-cyan-500/10 hover:bg-cyan-500/10 transition-all flex justify-between items-center group cursor-pointer rounded"
             data-index="${index}">
            <div class="flex-1">
                <div class="font-bold text-cyan-300 group-hover:text-cyan-100 flex items-center gap-2">
                    ${s.name}
                    <svg class="w-3 h-3 text-cyan-600 opacity-0 group-hover:opacity-100 transition-opacity" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path>
                    </svg>
                </div>
                <div class="text-xs text-cyan-600 mt-1 line-clamp-1">${s.description}</div>
            </div>
            <div class="text-right ml-3">
                <div class="text-xs font-mono ${s.enabled ? 'text-green-400' : 'text-gray-500'}">
                    ${s.enabled ? '● ACTIVE' : '○ OFFLINE'}
                </div>
                <div class="text-[10px] text-cyan-700 mt-1">${s.risk_level.toUpperCase()}</div>
            </div>
        </div>
    `).join('');

    // Add click handlers
    list.querySelectorAll('.strategy-item').forEach(item => {
        item.addEventListener('click', () => {
            const index = parseInt(item.dataset.index);
            openStrategyModal(strategies[index]);
        });
    });
}

// Strategy Modal Functions
async function openStrategyModal(strategy) {
    const modal = document.getElementById('strategy-modal');
    if (!modal) return;

    // Show loading state
    document.getElementById('modal-title').textContent = strategy.name;
    document.getElementById('modal-description').textContent = 'Loading details...';
    modal.classList.remove('hidden');
    modal.classList.add('flex');

    // Fetch detailed data from API
    try {
        const detail = await api.getStrategyDetail(strategy.name);
        
        if (!detail.success) {
            document.getElementById('modal-description').textContent = 'Failed to load strategy details';
            return;
        }

        // Populate modal data
        document.getElementById('modal-description').textContent = detail.description || 'No description available';
        
        const statusEl = document.getElementById('modal-status');
        statusEl.textContent = detail.enabled ? 'ACTIVE' : 'OFFLINE';
        statusEl.className = `px-2 py-1 text-xs font-mono rounded ${detail.enabled ? 'bg-green-500/20 text-green-400' : 'bg-gray-500/20 text-gray-400'}`;
        
        document.getElementById('modal-risk').textContent = `RISK: ${(detail.risk_level || 'unknown').toUpperCase()}`;
        
        const roi = detail.roi_estimate;
        document.getElementById('modal-roi').textContent = roi ? `${roi > 0 ? '+' : ''}${roi.toFixed(2)}%` : 'N/A';
        document.getElementById('modal-roi').className = `text-lg font-bold font-orbitron ${roi > 0 ? 'text-green-400' : roi < 0 ? 'text-red-400' : 'text-cyan-300'}`;
        
        document.getElementById('modal-executions').textContent = detail.executions || 0;
        document.getElementById('modal-success-rate').textContent = detail.success_rate || 'N/A';
        
        // Update avg P&L
        const avgPnl = detail.avg_pnl || 0;
        const avgPnlEl = document.getElementById('modal-avg-pnl');
        avgPnlEl.textContent = `${avgPnl >= 0 ? '+' : ''}$${avgPnl.toFixed(2)}`;
        avgPnlEl.className = `text-lg font-bold font-orbitron ${avgPnl > 0 ? 'text-green-400' : avgPnl < 0 ? 'text-red-400' : 'text-cyan-300'}`;
        
        // Update 24h stats
        document.getElementById('modal-recent-count').textContent = detail.recent_executions_24h || 0;
        const recentPnl = detail.recent_pnl_24h || 0;
        const recentPnlEl = document.getElementById('modal-recent-pnl');
        recentPnlEl.textContent = `${recentPnl >= 0 ? '+' : ''}$${recentPnl.toFixed(2)}`;
        recentPnlEl.className = `text-sm font-mono ${recentPnl > 0 ? 'text-green-400' : recentPnl < 0 ? 'text-red-400' : 'text-cyan-300'}`;
        
        // Render history
        const historyContainer = document.getElementById('modal-history');
        if (detail.history && detail.history.length > 0) {
            historyContainer.innerHTML = detail.history.map(h => `
                <div class="flex justify-between items-center p-2 bg-cyan-950/20 rounded border border-cyan-500/10 text-xs hover:bg-cyan-900/30 transition-colors">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full ${h.success ? 'bg-green-400' : 'bg-red-400'} ${h.success ? 'shadow-[0_0_6px_rgba(74,222,128,0.6)]' : 'shadow-[0_0_6px_rgba(248,113,113,0.6)]'}"></span>
                        <span class="text-cyan-300">${h.action}</span>
                    </div>
                    <div class="text-right">
                        <div class="text-cyan-400 font-mono ${h.result.startsWith('+') ? 'text-green-400' : h.result.startsWith('-') ? 'text-red-400' : ''}">${h.result}</div>
                        <div class="text-cyan-700 text-[10px]">${h.time}</div>
                    </div>
                </div>
            `).join('');
        } else {
            historyContainer.innerHTML = '<div class="text-center text-cyan-700 text-xs py-4">No execution history available</div>';
        }

        // Setup toggle button
        const toggleBtn = document.getElementById('modal-toggle');
        toggleBtn.textContent = detail.enabled ? 'DISABLE' : 'ENABLE';
        toggleBtn.onclick = () => toggleStrategy(strategy.name, !detail.enabled);

        // Load version history
        loadStrategyVersions(strategy.name);

    } catch (e) {
        console.error('Failed to load strategy details:', e);
        document.getElementById('modal-description').textContent = 'Error loading details';
    }
}

function closeStrategyModal() {
    const modal = document.getElementById('strategy-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
}

function generateMockHistory(strategyName) {
    const actions = ['Buy Signal', 'Sell Signal', 'Position Adjust', 'Risk Check', 'Rebalance'];
    const history = [];
    for (let i = 0; i < 5; i++) {
        const date = new Date();
        date.setHours(date.getHours() - i * 2);
        history.push({
            action: actions[Math.floor(Math.random() * actions.length)],
            success: Math.random() > 0.2,
            result: `${Math.random() > 0.5 ? '+' : '-'}${(Math.random() * 100).toFixed(2)}`,
            time: date.toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' })
        });
    }
    return history;
}

async function toggleStrategy(name, enable) {
    // This will be implemented with real API call
    console.log(`Toggling strategy ${name} to ${enable ? 'enabled' : 'disabled'}`);
    // For now, just close the modal
    closeStrategyModal();
    // Refresh the list
    const strategies = await api.getStrategies();
    renderStrategies(strategies);
}

// Setup modal event listeners
document.addEventListener('DOMContentLoaded', () => {
    const closeBtn = document.getElementById('close-modal');
    const closeBtn2 = document.getElementById('close-modal-btn');
    const modal = document.getElementById('strategy-modal');
    
    if (closeBtn) closeBtn.addEventListener('click', closeStrategyModal);
    if (closeBtn2) closeBtn2.addEventListener('click', closeStrategyModal);
    
    // Close on backdrop click
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeStrategyModal();
        });
    }
    
    // Close on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeStrategyModal();
    });
});

async function handleChatSubmit(e) {
    e.preventDefault();
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;

    // Add user message to UI
    addChatMessage('USER', msg, 'user');
    input.value = '';

    // Check if there's a pending strategy confirmation
    if (pendingStrategyConfirmation) {
        handleStrategyConfirmation(msg);
        return;
    }

    // Check if this is a strategy management message
    const strategyKeywords = [
        '策略', '创建', '生成', '优化', '启用', '禁用', '删除', '列出',
        '版本', '回滚', '迭代', '改进', '修改',
        'strategy', 'create', 'generate', 'optimize', 'enable',
        'disable', 'delete', 'list', 'template', '模板',
        'version', 'rollback', 'iterate', 'improve',
        'analyze', 'analysis', 'recommend', 'recommendation', 'review'
    ];

    const isStrategyMessage = strategyKeywords.some(kw =>
        msg.toLowerCase().includes(kw.toLowerCase())
    );

    if (isStrategyMessage) {
        const analysisKeywords = ['analyze', 'analysis', 'recommend', 'recommendation', 'review'];
        const isAnalysisMessage = analysisKeywords.some(kw => msg.toLowerCase().includes(kw));

        if (isAnalysisMessage) {
            await handleStrategyAnalysis(msg);
            return;
        }

        await handleStrategyMessage(msg);
        return;
    }

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

async function handleStrategyAnalysis(msg) {
    try {
        const normalized = msg.toLowerCase();
        let strategyName = null;

        const strategyCandidates = [
            'crypto_dca',
            'crypto_funding',
            'crypto_price_alert',
            'smart_rebalance',
            'stock_dividend',
            'appl_trading_strategy',
            'gold_trading_strategy'
        ];

        for (const candidate of strategyCandidates) {
            if (normalized.includes(candidate.toLowerCase())) {
                strategyName = candidate;
                break;
            }
        }

        const thinkingId = addChatMessage('ANALYSIS', strategyName ? `Reviewing ${strategyName}...` : 'Reviewing all strategies...', 'system', true);
        const data = await api.analyzeStrategies(strategyName);
        removeChatMessage(thinkingId);

        if (!data.success) {
            addChatMessage('ANALYSIS', data.error || 'Analysis failed.', 'error');
            return true;
        }

        const lines = [];
        if (data.overview) {
            lines.push(`Strategy review complete. ${data.overview.high_priority} high, ${data.overview.medium_priority} medium, ${data.overview.low_priority} low priority.`);
            lines.push('');
        }

        for (const item of data.analyses || []) {
            lines.push(`## ${item.strategy_name}`);
            lines.push(`- Priority: ${String(item.priority).toUpperCase()}`);
            lines.push(`- Enabled: ${item.enabled ? 'yes' : 'no'}`);
            lines.push(`- Risk: ${item.risk_level}`);
            lines.push(`- Executions: ${item.summary.total_executions}, success rate: ${Number(item.summary.success_rate).toFixed(0)}%, avg P&L: ${Number(item.summary.avg_pnl).toFixed(2)}`);
            lines.push(`- Recommended agents: ${(item.recommended_agents || []).join(', ')}`);
            lines.push('- Concerns:');
            for (const concern of item.concerns || []) {
                lines.push(`  - ${concern}`);
            }
            lines.push('- Recommendations:');
            for (const rec of item.recommendations || []) {
                lines.push(`  - ${rec}`);
            }
            lines.push('');
        }

        addChatMessage('ANALYSIS', lines.join('\n'), 'system');
        return true;
    } catch (e) {
        console.error('Strategy analysis error:', e);
        addChatMessage('ANALYSIS', 'Error analyzing strategies.', 'error');
        return true;
    }
}

async function handleStrategyMessage(msg) {
    try {
        const thinkingId = addChatMessage('AI STRATEGY', 'Processing...', 'system', true);

        const response = await api.strategyChat(msg);

        removeChatMessage(thinkingId);

        if (response.success) {
            addChatMessage('AI STRATEGY', response.message, 'system');

            // Check if confirmation is needed
            if (response.data && response.data.pending_confirm) {
                pendingStrategyConfirmation = response.data.strategy;
                addChatMessage('AI STRATEGY', '💡 Reply "yes" to confirm saving this strategy, or anything else to cancel.', 'system');
            }
            
            // If strategy was auto-created, refresh the list immediately
            if (response.data && response.data.strategy_name) {
                const refreshId = addChatMessage('SYSTEM', '🔄 Updating strategy list...', 'system', true);
                
                // Wait a bit for backend to finish registering
                await new Promise(r => setTimeout(r, 500));
                await updateStatus();
                
                removeChatMessage(refreshId);
                
                if (response.data.auto_loaded) {
                    addChatMessage('SYSTEM', `✅ **${response.data.strategy_name}** is now active!`, 'system');
                } else {
                    addChatMessage('SYSTEM', `✅ **${response.data.strategy_name}** saved. Restart to activate.`, 'system');
                }
            }
        } else {
            addChatMessage('AI STRATEGY', response.message, 'error');
        }

    } catch (e) {
        console.error('Strategy chat error:', e);
        addChatMessage('SYSTEM', 'Error processing strategy command.', 'error');
    }
}

async function handleStrategyConfirmation(msg) {
    const confirmed = ['yes', '是', 'y', '确认', 'save', '保存'].includes(msg.toLowerCase().trim());

    if (confirmed && pendingStrategyConfirmation) {
        try {
            const thinkingId = addChatMessage('AI STRATEGY', 'Saving strategy...', 'system', true);

            const response = await api.confirmSaveStrategy(pendingStrategyConfirmation);

            removeChatMessage(thinkingId);
            addChatMessage('AI STRATEGY', response.message, response.success ? 'system' : 'error');

            // Refresh strategies list
            updateStatus();
        } catch (e) {
            addChatMessage('SYSTEM', 'Error saving strategy.', 'error');
        }
    } else {
        addChatMessage('AI STRATEGY', '❎ Strategy save cancelled.', 'system');
    }

    pendingStrategyConfirmation = null;
}

async function loadStrategyTemplates() {
    try {
        const data = await api.getStrategyTemplates();
        console.log('Strategy templates loaded:', data.templates);
        // Templates are available for UI enhancement
    } catch (e) {
        console.warn('Could not load strategy templates:', e);
    }
}

function addChatMessage(sender, text, type = 'system', isTemporary = false) {
    const history = document.getElementById('chat-history');
    const id = 'msg-' + Date.now();
    const isUser = type === 'user';
    const alignClass = isUser ? 'ml-auto border-r-2 rounded-l-lg rounded-br-lg bg-cyan-600/20 text-right' : 'mr-auto border-l-2 rounded-r-lg rounded-bl-lg bg-cyan-900/20 text-left';
    const borderClass = isUser ? 'border-cyan-400' : 'border-cyan-500';

    // Parse markdown for non-user messages
    let contentHtml;
    if (isUser) {
        // User messages: plain text with escape
        contentHtml = escapeHtml(text);
    } else {
        // AI/System messages: parse markdown
        contentHtml = parseMarkdown(text);
    }

    const html = `
        <div id="${id}" class="chat-msg ${type} ${alignClass} ${borderClass} p-3 max-w-[90%] backdrop-blur-sm animate-fade-in-up">
            <div class="text-[10px] text-cyan-500 font-bold mb-1 tracking-widest opacity-70">${sender}</div>
            <div class="chat-content text-cyan-100 prose prose-invert prose-sm max-w-none">${contentHtml}</div>
        </div>
    `;
    history.insertAdjacentHTML('beforeend', html);
    
    // Smooth scroll to bottom
    scrollToBottom(history);
    return id;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function parseMarkdown(text) {
    if (typeof marked === 'undefined') {
        // Fallback if marked.js is not loaded
        return escapeHtml(text).replace(/\n/g, '<br>');
    }
    
    // Configure marked options
    marked.setOptions({
        breaks: true,        // Convert single newlines to <br>
        gfm: true,           // GitHub Flavored Markdown
        headerIds: false,    // Don't add ids to headers
        mangle: false,       // Don't mangle email addresses
        sanitize: false,     // We use DOMPurify-like approach with escapeHtml for user content
        smartLists: true,
        smartypants: true,
        xhtml: false
    });
    
    return marked.parse(text);
}

function scrollToBottom(element) {
    // Use requestAnimationFrame to ensure DOM is updated
    requestAnimationFrame(() => {
        element.scrollTo({
            top: element.scrollHeight,
            behavior: 'smooth'
        });
    });
    
    // Fallback: ensure scroll happens after any images/code blocks load
    setTimeout(() => {
        element.scrollTo({
            top: element.scrollHeight,
            behavior: 'smooth'
        });
    }, 100);
}

function removeChatMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

// Strategy Version Management Functions
async function loadStrategyVersions(strategyName) {
    try {
        const data = await api.getStrategyVersions(strategyName);
        if (data.success) {
            renderVersionHistory(data.versions, strategyName);
        }
    } catch (e) {
        console.error('Failed to load versions:', e);
    }
}

function renderVersionHistory(versions, strategyName) {
    const container = document.getElementById('version-history');
    if (!container) return;

    if (!versions || versions.length === 0) {
        container.innerHTML = '<div class="text-center text-cyan-700 text-xs py-4">No version history available</div>';
        return;
    }

    container.innerHTML = versions.map((v, i) => `
        <div class="flex justify-between items-center p-2 bg-cyan-950/20 rounded border border-cyan-500/10 text-xs hover:bg-cyan-900/30 transition-colors group">
            <div class="flex items-center gap-2">
                <span class="text-cyan-500 font-mono">${v.version_id.substring(0, 8)}</span>
                <span class="text-cyan-600">${v.created_at.substring(0, 10)}</span>
                ${v.author === 'ai' ? '<span class="text-xs text-purple-400">🤖 AI</span>' : 
                  v.author === 'user' ? '<span class="text-xs text-blue-400">👤 User</span>' : 
                  '<span class="text-xs text-orange-400">⚙️ System</span>'}
                ${v.tags && v.tags.includes('iteration') ? '<span class="text-xs text-green-400">🔄 Iteration</span>' : ''}
            </div>
            <div class="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <button onclick="viewVersionCode('${strategyName}', '${v.version_id}')" 
                        class="px-2 py-1 bg-cyan-700/30 hover:bg-cyan-600/50 rounded text-cyan-300 transition-colors">
                    View
                </button>
                <button onclick="rollbackToVersion('${strategyName}', '${v.version_id}')" 
                        class="px-2 py-1 bg-orange-700/30 hover:bg-orange-600/50 rounded text-orange-300 transition-colors">
                    Rollback
                </button>
            </div>
        </div>
    `).join('');
}

async function viewVersionCode(strategyName, versionId) {
    try {
        const data = await api.getVersionCode(strategyName, versionId);
        if (data.success) {
            // Create a modal to display the code
            const modal = document.createElement('div');
            modal.className = 'fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4';
            modal.innerHTML = `
                <div class="bg-cyan-950/90 border border-cyan-500/30 rounded-lg max-w-4xl w-full max-h-[90vh] flex flex-col">
                    <div class="flex justify-between items-center p-4 border-b border-cyan-500/20">
                        <h3 class="text-cyan-300 font-bold">Version ${versionId.substring(0, 8)} - ${strategyName}</h3>
                        <button onclick="this.closest('.fixed').remove()" class="text-cyan-500 hover:text-cyan-300">
                            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                            </svg>
                        </button>
                    </div>
                    <div class="flex-1 overflow-auto p-4">
                        <pre class="text-xs text-cyan-300 font-mono whitespace-pre-wrap">${escapeHtml(data.code)}</pre>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        }
    } catch (e) {
        console.error('Failed to load version code:', e);
    }
}

async function rollbackToVersion(strategyName, versionId) {
    if (!confirm(`Rollback ${strategyName} to version ${versionId.substring(0, 8)}?\n\nThis will replace the current code with the selected version.`)) {
        return;
    }

    try {
        const data = await api.rollbackStrategy(strategyName, versionId);
        if (data.success) {
            alert(`Successfully rolled back to version ${versionId.substring(0, 8)}\nPlease restart MoneyClaw to apply the changes.`);
            loadStrategyVersions(strategyName);
        } else {
            alert('Rollback failed: ' + (data.error || 'Unknown error'));
        }
    } catch (e) {
        console.error('Rollback failed:', e);
        alert('Rollback failed: ' + e.message);
    }
}

// Make functions available globally for onclick handlers
window.loadStrategyVersions = loadStrategyVersions;
window.viewVersionCode = viewVersionCode;
window.rollbackToVersion = rollbackToVersion;
