export class Api {
    async getStatus() {
        const res = await fetch('/api/status');
        return await res.json();
    }

    async getStrategies() {
        const res = await fetch('/api/strategies');
        return await res.json();
    }

    async getHistory() {
        const res = await fetch('/api/history');
        return await res.json();
    }

    // Strategy Management API
    async strategyChat(message) {
        const res = await fetch('/api/strategy/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message })
        });
        return await res.json();
    }

    async confirmSaveStrategy(strategy) {
        const res = await fetch('/api/strategy/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ strategy })
        });
        return await res.json();
    }

    async getStrategyTemplates() {
        const res = await fetch('/api/strategy/templates');
        return await res.json();
    }

    async getStrategyDetail(name) {
        const res = await fetch(`/api/strategies/${encodeURIComponent(name)}`);
        return await res.json();
    }

    // Strategy Version Management API
    async getStrategyVersions(strategyName) {
        const res = await fetch(`/api/strategy/${encodeURIComponent(strategyName)}/versions`);
        return await res.json();
    }

    async rollbackStrategy(strategyName, versionId) {
        const res = await fetch(`/api/strategy/${encodeURIComponent(strategyName)}/rollback`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ version_id: versionId })
        });
        return await res.json();
    }

    async getVersionCode(strategyName, versionId) {
        const res = await fetch(`/api/strategy/${encodeURIComponent(strategyName)}/version/${encodeURIComponent(versionId)}/code`);
        return await res.json();
    }

    async getAllStrategyVersions() {
        const res = await fetch('/api/strategy/versions/all');
        return await res.json();
    }
}
