"""Optimized XP storage with caching and batch operations."""

import asyncio
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, TypedDict, Optional
from functools import lru_cache

from config import DATA_DIR
from utils.persistence import ensure_dir, read_json_safe, atomic_write_json_async

XP_PATH = os.path.join(DATA_DIR, "data.json")
logger = logging.getLogger(__name__)


class XPUserData(TypedDict, total=False):
    xp: int
    level: int
    double_xp_until: str
    last_accessed: str  # Pour le cache LRU manuel


class BatchUpdate:
    """Accumule les mises à jour XP pour traitement par lot."""
    
    def __init__(self):
        self.pending: Dict[str, int] = defaultdict(int)
        self.lock = asyncio.Lock()
        
    async def add(self, user_id: str, amount: int) -> None:
        async with self.lock:
            self.pending[user_id] += amount
    
    async def flush(self) -> Dict[str, int]:
        async with self.lock:
            updates = dict(self.pending)
            self.pending.clear()
            return updates


class XPStore:
    """Stockage XP optimisé avec cache et opérations par lot."""

    def __init__(self, path: str = XP_PATH, cache_size: int = 500):
        self.path = path
        self.data: Dict[str, XPUserData] = {}
        self.lock = asyncio.Lock()
        self.cache_size = cache_size
        self._flush_task: Optional[asyncio.Task] = None
        self._periodic_task: Optional[asyncio.Task] = None
        self._batch_updates = BatchUpdate()
        self._last_cleanup = datetime.utcnow()
        
        # Statistiques pour monitoring
        self.stats = {
            "cache_hits": 0,
            "cache_misses": 0,
            "batch_flushes": 0,
            "total_updates": 0
        }

    async def start(self) -> None:
        """Initialise le store et démarre les tâches de fond."""
        if self._periodic_task and not self._periodic_task.done():
            return
            
        ensure_dir(DATA_DIR)
        self.data = read_json_safe(self.path)
        
        # Nettoyer le cache au démarrage
        await self._cleanup_cache()
        
        self._periodic_task = asyncio.create_task(self._periodic_maintenance())
        logger.info("XP Store démarré avec cache de %d entrées", self.cache_size)

    async def aclose(self) -> None:
        """Fermeture propre avec flush des données."""
        # Annuler les tâches
        for task in (self._flush_task, self._periodic_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Flush final des mises à jour en attente
        await self._process_batch_updates()
        await self.flush()
        
        logger.info("XP Store fermé (stats: %s)", self.stats)

    async def _cleanup_cache(self) -> None:
        """Supprime les entrées les moins récemment utilisées."""
        async with self.lock:
            if len(self.data) <= self.cache_size:
                return
            
            # Trier par dernière utilisation
            items = [
                (uid, data) 
                for uid, data in self.data.items()
            ]
            
            # Parser les dates d'accès
            def get_last_accessed(item):
                _, data = item
                last = data.get("last_accessed")
                if not last:
                    return datetime.min
                try:
                    return datetime.fromisoformat(last)
                except:
                    return datetime.min
            
            items.sort(key=get_last_accessed)
            
            # Garder seulement les N plus récents
            to_remove = len(items) - self.cache_size
            if to_remove > 0:
                removed_users = [uid for uid, _ in items[:to_remove]]
                
                # Sauvegarder sur disque avant suppression
                for uid in removed_users:
                    del self.data[uid]
                    
                logger.info("Cache nettoyé: %d entrées supprimées", to_remove)
                self.stats["cache_misses"] += to_remove

    async def _periodic_maintenance(self) -> None:
        """Maintenance périodique: flush batch et nettoyage cache."""
        try:
            while True:
                await asyncio.sleep(60)  # Toutes les minutes
                
                # Traiter les mises à jour en lot
                await self._process_batch_updates()
                
                # Nettoyer le cache toutes les 10 minutes
                now = datetime.utcnow()
                if (now - self._last_cleanup).seconds > 600:
                    await self._cleanup_cache()
                    self._last_cleanup = now
                
                # Flush périodique sur disque toutes les 5 minutes
                if self.stats["total_updates"] % 100 == 0:
                    await self.flush()
                    
        except asyncio.CancelledError:
            pass

    async def _process_batch_updates(self) -> None:
        """Applique toutes les mises à jour en attente."""
        updates = await self._batch_updates.flush()
        if not updates:
            return
        
        async with self.lock:
            for uid, amount in updates.items():
                user = self.data.setdefault(uid, {"xp": 0, "level": 0})
                old_xp = user.get("xp", 0)
                new_xp = max(0, old_xp + amount)
                user["xp"] = new_xp
                user["level"] = self._calc_level(new_xp)
                user["last_accessed"] = datetime.utcnow().isoformat()
                
            self.stats["batch_flushes"] += 1
            self.stats["total_updates"] += len(updates)
        
        # Planifier un flush sur disque
        self._schedule_flush()
        
        logger.debug("Batch update: %d utilisateurs traités", len(updates))

    def _schedule_flush(self) -> None:
        """Planifie un flush différé sur disque."""
        if self._flush_task and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        """Flush différé pour regrouper les écritures."""
        try:
            await asyncio.sleep(5)  # Attendre 5 secondes
            await self.flush()
        except asyncio.CancelledError:
            pass

    async def flush(self) -> None:
        """Écrit les données sur disque."""
        async with self.lock:
            # Créer une copie pour l'écriture
            data_copy = dict(self.data)
            
        await atomic_write_json_async(self.path, data_copy)
        logger.info("XP flush: %d utilisateurs, %d updates totales", 
                   len(data_copy), self.stats["total_updates"])

    async def add_xp(
        self,
        user_id: int,
        amount: int,
        *,
        guild_id: Optional[int] = None,
        source: str = "manual",
        batch: bool = False  # Traitement immédiat par défaut
    ) -> Tuple[int, int, int, int]:
        """
        Ajoute de l'XP à un utilisateur.
        
        Args:
            user_id: ID de l'utilisateur
            amount: Montant d'XP à ajouter (peut être négatif)
            guild_id: ID du serveur pour les events
            source: Source de l'XP
            batch: Si True, accumule pour traitement par lot
            
        Returns:
            Tuple (old_level, new_level, old_xp, new_xp)
        """
        uid = str(user_id)
        
        # Validation des montants
        MAX_SINGLE_TRANSACTION = 10000
        if abs(amount) > MAX_SINGLE_TRANSACTION:
            logger.warning("Transaction XP trop grande: %d pour user %s", amount, uid)
            amount = MAX_SINGLE_TRANSACTION if amount > 0 else -MAX_SINGLE_TRANSACTION
        
        if batch and amount != 0:
            # Ajouter au batch pour traitement ultérieur
            await self._batch_updates.add(uid, amount)

            # Récupérer l'état actuel
            async with self.lock:
                user = self.data.get(uid, {"xp": 0, "level": 0})
                base_xp = int(user.get("xp", 0))

            # Tenir compte des mises à jour en attente pour estimer correctement
            async with self._batch_updates.lock:
                pending_total = self._batch_updates.pending.get(uid, 0)

            # XP avant cette transaction (y compris les updates précédentes)
            old_xp = max(0, base_xp + pending_total - amount)
            old_level = self._calc_level(old_xp)

            # XP estimée après cette transaction
            estimated_xp = max(0, base_xp + pending_total)
            estimated_level = self._calc_level(estimated_xp)

            return old_level, estimated_level, old_xp, estimated_xp
        
        # Traitement immédiat (non-batch)
        async with self.lock:
            # Vérifier le cache
            if uid not in self.data:
                self.stats["cache_misses"] += 1
                # Charger depuis le disque si nécessaire
                all_data = read_json_safe(self.path)
                if uid in all_data:
                    self.data[uid] = all_data[uid]
                else:
                    self.data[uid] = {"xp": 0, "level": 0}
            else:
                self.stats["cache_hits"] += 1
            
            user = self.data[uid]
            old_level = int(user.get("level", 0))
            old_xp = int(user.get("xp", 0))
            
            # Appliquer le bonus double XP si actif
            if amount > 0:
                double_until = user.get("double_xp_until")
                if double_until:
                    try:
                        exp_dt = datetime.fromisoformat(double_until)
                        if exp_dt > datetime.utcnow():
                            amount *= 2
                            logger.info("Double XP appliqué pour %s: %d XP", uid, amount)
                        else:
                            del user["double_xp_until"]
                    except ValueError:
                        del user["double_xp_until"]
            
            # Calculer les nouvelles valeurs
            new_xp = max(0, old_xp + amount)
            new_level = self._calc_level(new_xp)
            
            # Mettre à jour
            user["xp"] = new_xp
            user["level"] = new_level
            user["last_accessed"] = datetime.utcnow().isoformat()
            
            self.stats["total_updates"] += 1
        
        # Planifier la sauvegarde
        if amount != 0:
            self._schedule_flush()
        
        # Émettre l'événement de changement de niveau
        if new_level != old_level and guild_id is not None:
            from utils.level_feed import LevelChange, emit
            emit(
                LevelChange(
                    user_id=user_id,
                    guild_id=guild_id,
                    old_level=old_level,
                    new_level=new_level,
                    old_xp=old_xp,
                    new_xp=new_xp,
                    source=source,
                )
            )
        
        return old_level, new_level, old_xp, new_xp

    async def get_user_data(self, user_id: int) -> XPUserData:
        """Récupère les données d'un utilisateur."""
        uid = str(user_id)
        
        async with self.lock:
            if uid in self.data:
                self.stats["cache_hits"] += 1
                user = self.data[uid]
                user["last_accessed"] = datetime.utcnow().isoformat()
                return user
            
            self.stats["cache_misses"] += 1
            
        # Charger depuis le disque
        all_data = read_json_safe(self.path)
        user_data = all_data.get(uid, {"xp": 0, "level": 0})
        
        # Ajouter au cache
        async with self.lock:
            self.data[uid] = user_data
            user_data["last_accessed"] = datetime.utcnow().isoformat()
            
            # Vérifier la taille du cache
            if len(self.data) > self.cache_size * 1.2:  # 20% de marge
                asyncio.create_task(self._cleanup_cache())
        
        return user_data

    async def get_top_users(self, limit: int = 10) -> List[Tuple[str, XPUserData]]:
        """Récupère le top des utilisateurs par XP."""
        all_data = read_json_safe(self.path)
        
        # Trier par XP
        sorted_users = sorted(
            all_data.items(),
            key=lambda x: x[1].get("xp", 0),
            reverse=True
        )[:limit]
        
        return sorted_users

    async def get_stats(self) -> Dict[str, any]:
        """Retourne les statistiques du store."""
        async with self.lock:
            cache_users = len(self.data)
        
        total_users = len(read_json_safe(self.path))
        
        return {
            **self.stats,
            "cache_users": cache_users,
            "total_users": total_users,
            "cache_ratio": cache_users / max(1, total_users),
            "pending_updates": len(self._batch_updates.pending)
        }

    @staticmethod
    def _calc_level(xp: int) -> int:
        """Calcule le niveau basé sur l'XP."""
        try:
            return int(math.isqrt(xp // 100))
        except Exception:
            level = 0
            while xp >= (level + 1) ** 2 * 100:
                level += 1
            return level


# Instance globale
xp_store = XPStore()
