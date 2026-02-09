"""Strategy Version Manager — 策略版本管理.

提供策略代码的版本控制功能：
1. 保存新版本
2. 查询版本历史
3. 版本回滚
4. 版本对比
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger()


@dataclass
class StrategyVersion:
    """策略版本信息."""

    version_id: str
    strategy_name: str
    created_at: str
    code_hash: str
    code_preview: str
    description: str
    author: str  # 'user' | 'ai' | 'optimizer'
    change_summary: str
    performance_snapshot: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


class StrategyVersionManager:
    """策略版本管理器.
    
    管理策略代码的版本历史，支持回滚和对比。
    """

    def __init__(self, strategies_dir: str | Path = "strategies") -> None:
        self._strategies_dir = Path(strategies_dir)
        self._versions_dir = self._strategies_dir / ".versions"
        self._versions_dir.mkdir(parents=True, exist_ok=True)

    def _get_strategy_versions_dir(self, strategy_name: str) -> Path:
        """获取指定策略的版本目录."""
        versions_dir = self._versions_dir / strategy_name
        versions_dir.mkdir(parents=True, exist_ok=True)
        return versions_dir

    def _generate_version_id(self, code: str) -> str:
        """基于代码内容生成版本ID."""
        timestamp = datetime.now().isoformat()
        hash_input = f"{code}{timestamp}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    def _generate_code_hash(self, code: str) -> str:
        """生成代码内容的哈希."""
        return hashlib.sha256(code.encode()).hexdigest()[:16]

    def save_version(
        self,
        strategy_name: str,
        code: str,
        description: str,
        author: str = "ai",
        change_summary: str = "",
        performance: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> StrategyVersion:
        """保存策略新版本.

        Args:
            strategy_name: 策略名称
            code: 策略代码
            description: 策略描述
            author: 版本创建者 ('user' | 'ai' | 'optimizer')
            change_summary: 变更摘要
            performance: 性能快照
            tags: 版本标签

        Returns:
            版本信息对象
        """
        versions_dir = self._get_strategy_versions_dir(strategy_name)
        
        # 生成版本ID和代码哈希
        version_id = self._generate_version_id(code)
        code_hash = self._generate_code_hash(code)
        
        # 检查是否已有相同代码的版本
        existing = self._find_version_by_hash(strategy_name, code_hash)
        if existing:
            log.info("strategy_version.duplicate", strategy=strategy_name, version_id=existing.version_id)
            return existing

        created_at = datetime.now().isoformat()
        
        # 创建版本信息
        version = StrategyVersion(
            version_id=version_id,
            strategy_name=strategy_name,
            created_at=created_at,
            code_hash=code_hash,
            code_preview=code[:500] + "..." if len(code) > 500 else code,
            description=description,
            author=author,
            change_summary=change_summary,
            performance_snapshot=performance or {},
            tags=tags or [],
        )

        # 保存版本文件
        version_file = versions_dir / f"{version_id}.json"
        with open(version_file, "w", encoding="utf-8") as f:
            json.dump(asdict(version), f, indent=2, ensure_ascii=False)

        # 保存完整代码
        code_file = versions_dir / f"{version_id}.py"
        code_file.write_text(code, encoding="utf-8")

        # 更新版本索引
        self._update_version_index(strategy_name, version)

        log.info(
            "strategy_version.saved",
            strategy=strategy_name,
            version_id=version_id,
            author=author,
        )
        return version

    def _find_version_by_hash(self, strategy_name: str, code_hash: str) -> StrategyVersion | None:
        """通过代码哈希查找已存在的版本."""
        versions = self.list_versions(strategy_name)
        for v in versions:
            if v.code_hash == code_hash:
                return v
        return None

    def _update_version_index(self, strategy_name: str, version: StrategyVersion) -> None:
        """更新版本索引文件."""
        versions_dir = self._get_strategy_versions_dir(strategy_name)
        index_file = versions_dir / "index.json"
        
        index = []
        if index_file.exists():
            with open(index_file, "r", encoding="utf-8") as f:
                index = json.load(f)
        
        # 添加新版本到索引
        index.append({
            "version_id": version.version_id,
            "created_at": version.created_at,
            "author": version.author,
            "change_summary": version.change_summary,
            "code_hash": version.code_hash,
        })
        
        # 按时间排序
        index.sort(key=lambda x: x["created_at"], reverse=True)
        
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

    def list_versions(self, strategy_name: str) -> list[StrategyVersion]:
        """列出策略的所有版本.

        Args:
            strategy_name: 策略名称

        Returns:
            版本信息列表，按时间倒序排列
        """
        versions_dir = self._get_strategy_versions_dir(strategy_name)
        versions = []

        if not versions_dir.exists():
            return versions

        for version_file in versions_dir.glob("*.json"):
            if version_file.name == "index.json":
                continue
            try:
                with open(version_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                versions.append(StrategyVersion(**data))
            except Exception as e:
                log.warning("strategy_version.load_failed", file=str(version_file), error=str(e))

        # 按时间倒序排列
        versions.sort(key=lambda v: v.created_at, reverse=True)
        return versions

    def get_version(self, strategy_name: str, version_id: str) -> StrategyVersion | None:
        """获取指定版本的信息.

        Args:
            strategy_name: 策略名称
            version_id: 版本ID

        Returns:
            版本信息，如果不存在返回None
        """
        versions_dir = self._get_strategy_versions_dir(strategy_name)
        version_file = versions_dir / f"{version_id}.json"

        if not version_file.exists():
            return None

        try:
            with open(version_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return StrategyVersion(**data)
        except Exception as e:
            log.warning("strategy_version.get_failed", version_id=version_id, error=str(e))
            return None

    def get_version_code(self, strategy_name: str, version_id: str) -> str | None:
        """获取指定版本的代码.

        Args:
            strategy_name: 策略名称
            version_id: 版本ID

        Returns:
            代码内容，如果不存在返回None
        """
        versions_dir = self._get_strategy_versions_dir(strategy_name)
        code_file = versions_dir / f"{version_id}.py"

        if not code_file.exists():
            return None

        return code_file.read_text(encoding="utf-8")

    def rollback_to_version(
        self, strategy_name: str, version_id: str
    ) -> tuple[StrategyVersion, str] | None:
        """回滚到指定版本.

        Args:
            strategy_name: 策略名称
            version_id: 要回滚到的版本ID

        Returns:
            (版本信息, 代码内容)，如果失败返回None
        """
        version = self.get_version(strategy_name, version_id)
        if not version:
            log.error("strategy_version.rollback_not_found", version_id=version_id)
            return None

        code = self.get_version_code(strategy_name, version_id)
        if not code:
            log.error("strategy_version.rollback_code_not_found", version_id=version_id)
            return None

        # 保存当前版本（作为回滚前的备份）
        current_code_path = self._strategies_dir / strategy_name / "__init__.py"
        if current_code_path.exists():
            current_code = current_code_path.read_text(encoding="utf-8")
            self.save_version(
                strategy_name=strategy_name,
                code=current_code,
                description=f"Auto-backup before rollback to {version_id}",
                author="system",
                change_summary=f"自动备份：回滚到版本 {version_id[:8]} 之前的代码",
                tags=["auto-backup", "pre-rollback"],
            )

        log.info("strategy_version.rollback", strategy=strategy_name, to_version=version_id)
        return version, code

    def compare_versions(
        self, strategy_name: str, version_id1: str, version_id2: str
    ) -> dict[str, Any] | None:
        """对比两个版本的差异.

        Args:
            strategy_name: 策略名称
            version_id1: 第一个版本ID
            version_id2: 第二个版本ID

        Returns:
            对比结果，包含版本信息和差异统计
        """
        v1 = self.get_version(strategy_name, version_id1)
        v2 = self.get_version(strategy_name, version_id2)
        code1 = self.get_version_code(strategy_name, version_id1)
        code2 = self.get_version_code(strategy_name, version_id2)

        if not all([v1, v2, code1, code2]):
            return None

        # 简单的行数差异统计
        lines1 = code1.split("\n")
        lines2 = code2.split("\n")

        return {
            "version1": {
                "version_id": version_id1,
                "created_at": v1.created_at,
                "author": v1.author,
                "code_hash": v1.code_hash,
            },
            "version2": {
                "version_id": version_id2,
                "created_at": v2.created_at,
                "author": v2.author,
                "code_hash": v2.code_hash,
            },
            "code_stats": {
                "version1_lines": len(lines1),
                "version2_lines": len(lines2),
                "line_diff": len(lines2) - len(lines1),
            },
            "same_hash": v1.code_hash == v2.code_hash,
        }

    def get_latest_version(self, strategy_name: str) -> StrategyVersion | None:
        """获取策略的最新版本.

        Args:
            strategy_name: 策略名称

        Returns:
            最新版本信息，如果没有版本返回None
        """
        versions = self.list_versions(strategy_name)
        return versions[0] if versions else None

    def tag_version(
        self, strategy_name: str, version_id: str, tag: str
    ) -> bool:
        """为版本添加标签.

        Args:
            strategy_name: 策略名称
            version_id: 版本ID
            tag: 标签名称

        Returns:
            是否成功
        """
        version = self.get_version(strategy_name, version_id)
        if not version:
            return False

        if tag not in version.tags:
            version.tags.append(tag)
            
            versions_dir = self._get_strategy_versions_dir(strategy_name)
            version_file = versions_dir / f"{version_id}.json"
            with open(version_file, "w", encoding="utf-8") as f:
                json.dump(asdict(version), f, indent=2, ensure_ascii=False)

        return True

    def delete_version(self, strategy_name: str, version_id: str) -> bool:
        """删除指定版本.

        Args:
            strategy_name: 策略名称
            version_id: 版本ID

        Returns:
            是否成功
        """
        versions_dir = self._get_strategy_versions_dir(strategy_name)
        version_file = versions_dir / f"{version_id}.json"
        code_file = versions_dir / f"{version_id}.py"

        try:
            if version_file.exists():
                version_file.unlink()
            if code_file.exists():
                code_file.unlink()
            
            log.info("strategy_version.deleted", strategy=strategy_name, version_id=version_id)
            return True
        except Exception as e:
            log.error("strategy_version.delete_failed", version_id=version_id, error=str(e))
            return False

    def get_strategy_stats(self, strategy_name: str) -> dict[str, Any]:
        """获取策略的版本统计信息.

        Args:
            strategy_name: 策略名称

        Returns:
            统计信息
        """
        versions = self.list_versions(strategy_name)
        
        if not versions:
            return {"total_versions": 0}

        authors = {}
        for v in versions:
            authors[v.author] = authors.get(v.author, 0) + 1

        return {
            "total_versions": len(versions),
            "first_version": versions[-1].created_at,
            "latest_version": versions[0].created_at,
            "authors": authors,
            "tagged_versions": len([v for v in versions if v.tags]),
        }

    def list_all_strategies_with_versions(self) -> dict[str, list[StrategyVersion]]:
        """列出所有有版本历史的策略.

        Returns:
            策略名称到版本列表的映射
        """
        result = {}
        
        if not self._versions_dir.exists():
            return result

        for strategy_dir in self._versions_dir.iterdir():
            if strategy_dir.is_dir():
                strategy_name = strategy_dir.name
                versions = self.list_versions(strategy_name)
                if versions:
                    result[strategy_name] = versions

        return result
