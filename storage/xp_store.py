"""Optimized XP storage with SQLite persistence, caching and batch operations."""

import asyncio
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from config import DATA_DIR
from storage.db import SQLiteDatabase, database
from utils.persistence import atomic_write_json_async, ensure_dir, read_json_safe

XP_PATH = os.path.join(DATA_DIR, "data.json")
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _parse_utc_datetime(value: str) -> datetime:
    """Parse an ISO datetime and normalize legacy naive values to UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class XPUserData(TypedDict, total=False):
    xp: int
    level: int
    double_xp_until: str
    last_accessed: str  # Pour le cache LRU manuel


class BatchUpdate:
    """Accumule les mises à jour XP pour traitement par lot."""

    def __init__(self) -> None:
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
    """Stockage XP en mémoire avec persistance SQLite transactionnelle.

    ``self.data`` reste la source de vérité runtime afin de préserver tous les
    contrats historiques des cogs. Au démarrage, l'ancien fichier JSON est
    importé une seule fois dans SQLite, puis SQLite devient la source persistée.

    Les instances de test utilisant un chemin personnalisé reçoivent leur propre
    base SQLite à côté du JSON. Un ``flush()`` effectué avant ``start()`` garde
    volontairement le comportement JSON historique pour les tests bas niveau et
    les outils qui construisent un store sans démarrer son cycle de vie.
    """

    def __init__(
        self,
        path: str = XP_PATH,
        cache_size: int = 500,
        *,
        database_service: SQLiteDatabase | None = None,
    ) -> None:
        self.path = path
        self.data: Dict[str, XPUserData] = {}
        self.lock = asyncio.Lock()
        self.cache_size = cache_size
        self._flush_task: Optional[asyncio.Task] = None
        self._periodic_task: Optional[asyncio.Task] = None
        self._batch_updates = BatchUpdate()
        self._last_cleanup = _utc_now()
        self._last_flushed_update_count = 0
        self._started = False
        self._closing = False

        if database_service is not None:
            self._database = database_service
            self._owns_database = database_service is not database
        elif os.path.abspath(path) == os.path.abspath(XP_PATH):
            self._database = database
            self._owns_database = False
        else:
            self._database = SQLiteDatabase(Path(path).with_suffix(".db"))
            self._owns_database = True

        # Statistiques pour monitoring
        self.stats = {
            "cache_hits": 0,
            "cache_misses": 0,
            "batch_flushes": 0,
            "total_updates": 0,
        }

    async def start(self) -> None:
        """Charge SQLite et démarre les tâches de fond du store."""
        if self._periodic_task and not self._periodic_task.done():
            return

        self._closing = False
        ensure_dir(Path(self.path).parent)
        await self._database.start()
        await self._database.migrate_legacy_xp(self.path)
        loaded_data = await self._database.load_xp()
        self.data = {
            str(uid): dict(payload)  # type: ignore[assignment]
            for uid, payload in loaded_data.items()
        }
        self._started = True

        # ``self.data`` est la source de vérité runtime, pas un cache jetable.
        # La maintenance peut donc observer sa taille mais ne doit jamais évincer
        # des utilisateurs tant qu'un cache séparé n'existe pas.
        await self._cleanup_cache()

        self._periodic_task = asyncio.create_task(
            self._periodic_maintenance(),
            name="xp-store-maintenance",
        )
        logger.info(
            "XP Store démarré depuis SQLite avec %d utilisateurs",
            len(self.data),
        )

    async def aclose(self) -> None:
        """Fermeture propre avec flush transactionnel final."""
        self._closing = True

        # Annuler les tâches avant le flush final pour empêcher une nouvelle
        # sauvegarde différée de survivre à la fermeture du bot.
        for task in (self._flush_task, self._periodic_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Flush final des mises à jour en attente.
        await self._process_batch_updates()
        await self.flush()

        if self._owns_database:
            await self._database.aclose()
        self._started = False

        logger.info("XP Store fermé (stats: %s)", self.stats)

    async def _cleanup_cache(self) -> None:
        """Préserve toutes les entrées XP tant que ``data`` est la source de vérité.

        Historiquement, cette méthode supprimait les utilisateurs les moins
        récemment consultés lorsque ``len(self.data)`` dépassait ``cache_size``.
        Or :meth:`flush` persiste directement ``self.data``. L'éviction d'une
        entrée reviendrait donc à supprimer définitivement cet utilisateur du
        prochain snapshot persistant.

        ``cache_size`` reste conservé comme paramètre de compatibilité et pourra
        être réutilisé lorsqu'un véritable cache, distinct des données complètes,
        sera introduit. En attendant, cette opération est volontairement un
        no-op afin de garantir l'intégrité des données.
        """
        async with self.lock:
            if len(self.data) > self.cache_size:
                logger.debug(
                    "XP cache limit exceeded (%d > %d); preserving all users "
                    "because self.data is persistent state",
                    len(self.data),
                    self.cache_size,
                )

    def _has_unflushed_updates(self) -> bool:
        """Indique si des mutations XP sont postérieures au dernier flush réussi."""
        return self.stats["total_updates"] > self._last_flushed_update_count

    async def _periodic_maintenance(self) -> None:
        """Maintenance périodique: flush batch et nettoyage cache."""
        try:
            while True:
                await asyncio.sleep(60)  # Toutes les minutes

                # Traiter les mises à jour en lot
                await self._process_batch_updates()

                # Vérifier périodiquement la taille sans évincer de données.
                now = _utc_now()
                if (now - self._last_cleanup).total_seconds() > 600:
                    await self._cleanup_cache()
                    self._last_cleanup = now

                # Filet de sécurité : n'écrire que si des mises à jour n'ont pas
                # encore été incluses dans un flush réussi.
                if self._has_unflushed_updates():
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
                old_xp = int(user.get("xp", 0))
                new_xp = max(0, old_xp + amount)
                user["xp"] = new_xp
                user["level"] = self._calc_level(new_xp)
                user["last_accessed"] = _utc_now().isoformat()

            self.stats["batch_flushes"] += 1
            self.stats["total_updates"] += len(updates)

        # Planifier un flush sur le backend persistant.
        self._schedule_flush()

        logger.debug("Batch update: %d utilisateurs traités", len(updates))

    def _schedule_flush(self) -> None:
        """Planifie un flush différé sur le backend persistant."""
        if self._closing:
            return
        if self._flush_task and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(
            self._delayed_flush(),
            name="xp-store-delayed-flush",
        )

    async def _delayed_flush(self) -> None:
        """Flush différé pour regrouper les écritures."""
        try:
            await asyncio.sleep(5)  # Attendre 5 secondes
            await self.flush()
        except asyncio.CancelledError:
            pass

    async def flush(self) -> None:
        """Persiste un snapshot cohérent de l'état XP courant."""
        async with self.lock:
            # Copier également les payloads imbriqués afin qu'une mutation
            # postérieure au verrou ne puisse pas modifier le snapshot en cours.
            data_copy: Dict[str, XPUserData] = {
                uid: dict(payload) for uid, payload in self.data.items()
            }
            update_count = self.stats["total_updates"]

        if self._started:
            await self._database.upsert_xp(data_copy)
        else:
            # Compatibilité pour les tests/outils bas niveau qui utilisent un
            # XPStore sans appeler start(). La production démarre toujours le
            # store via RefugeBot.setup_hook() et persiste donc dans SQLite.
            await atomic_write_json_async(self.path, data_copy)

        self._last_flushed_update_count = max(
            self._last_flushed_update_count,
            update_count,
        )
        logger.info(
            "XP flush: %d utilisateurs, %d updates totales",
            len(data_copy),
            self.stats["total_updates"],
        )

    async def add_xp(
        self,
        user_id: int,
        amount: int,
        *,
        guild_id: Optional[int] = None,
        source: str = "manual",
        batch: bool = False,
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
        max_single_transaction = 10000
        if abs(amount) > max_single_transaction:
            logger.warning("Transaction XP trop grande: %d pour user %s", amount, uid)
            amount = max_single_transaction if amount > 0 else -max_single_transaction

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
            if uid not in self.data:
                self.stats["cache_misses"] += 1
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
                        exp_dt = _parse_utc_datetime(double_until)
                        if exp_dt > _utc_now():
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
            user["last_accessed"] = _utc_now().isoformat()

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

    async def try_spend_xp(
        self,
        user_id: int,
        amount: int,
        *,
        guild_id: Optional[int] = None,
        source: str = "spend",
    ) -> bool:
        """Débite ``amount`` XP seulement si le solde est suffisant.

        La vérification du solde et le débit sont effectués sous le même verrou
        que les autres mises à jour immédiates du store. Deux dépenses
        concurrentes ne peuvent donc pas consommer le même solde.

        Contrairement à :meth:`add_xp` avec un montant négatif, cette méthode ne
        rabat jamais silencieusement le solde à zéro : si l'utilisateur ne peut
        pas payer l'intégralité du montant, aucune XP n'est retirée et ``False``
        est retourné.
        """
        if amount < 0:
            raise ValueError("amount must be non-negative")
        if amount == 0:
            return True
        if amount > 10000:
            raise ValueError("amount exceeds maximum XP transaction")

        uid = str(user_id)
        old_level = 0
        new_level = 0
        old_xp = 0
        new_xp = 0

        async with self.lock:
            if uid not in self.data:
                self.stats["cache_misses"] += 1
                self.data[uid] = {"xp": 0, "level": 0}
            else:
                self.stats["cache_hits"] += 1

            user = self.data[uid]
            old_xp = int(user.get("xp", 0))
            old_level = int(user.get("level", self._calc_level(old_xp)))

            if old_xp < amount:
                user["last_accessed"] = _utc_now().isoformat()
                return False

            new_xp = old_xp - amount
            new_level = self._calc_level(new_xp)
            user["xp"] = new_xp
            user["level"] = new_level
            user["last_accessed"] = _utc_now().isoformat()
            self.stats["total_updates"] += 1

        self._schedule_flush()

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

        return True

    async def get_user_data(self, user_id: int) -> XPUserData:
        """Récupère une copie des données d'un utilisateur depuis la mémoire."""
        uid = str(user_id)
        should_check_size = False

        async with self.lock:
            if uid in self.data:
                self.stats["cache_hits"] += 1
            else:
                self.stats["cache_misses"] += 1
                self.data[uid] = {"xp": 0, "level": 0}
                should_check_size = len(self.data) > self.cache_size * 1.2

            user = self.data[uid]
            user["last_accessed"] = _utc_now().isoformat()
            user_data = dict(user)

        # ``_cleanup_cache`` est un no-op d'intégrité aujourd'hui, mais on garde
        # le déclencheur historique hors du verrou pour ne pas imbriquer les locks.
        if should_check_size:
            asyncio.create_task(self._cleanup_cache())

        return user_data

    async def get_top_users(self, limit: int = 10) -> List[Tuple[str, XPUserData]]:
        """Récupère le top depuis l'état mémoire chargé au démarrage."""
        async with self.lock:
            all_data = [
                (uid, dict(payload))
                for uid, payload in self.data.items()
            ]

        sorted_users = sorted(
            all_data,
            key=lambda x: int(x[1].get("xp", 0)),
            reverse=True,
        )[:limit]
        return sorted_users

    def read_json(self) -> Dict[str, XPUserData]:
        """Lit explicitement le snapshot JSON legacy de façon synchrone."""
        return read_json_safe(self.path)

    async def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques du store depuis l'état mémoire."""
        async with self.lock:
            cache_users = len(self.data)
            total_users = cache_users

        return {
            **self.stats,
            "cache_users": cache_users,
            "total_users": total_users,
            "cache_ratio": cache_users / max(1, total_users),
            "pending_updates": len(self._batch_updates.pending),
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


# Instance globale de production : SQLite partage le même lifecycle que le bot.
xp_store = XPStore(database_service=database)
