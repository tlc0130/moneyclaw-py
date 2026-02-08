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
}
