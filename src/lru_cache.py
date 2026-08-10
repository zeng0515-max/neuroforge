"""线程安全的 LRU Cache 实现。

使用双向链表 + 哈希表实现 O(1) 的 get/put 操作，
并通过 threading.RLock 保证线程安全。
"""

import threading
from collections import OrderedDict
from typing import Generic, Optional, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class _Node(Generic[K, V]):
    """双向链表节点。"""

    __slots__ = ("key", "value", "prev", "next")

    def __init__(self, key: K, value: V) -> None:
        self.key: K = key
        self.value: V = value
        self.prev: Optional[_Node[K, V]] = None
        self.next: Optional[_Node[K, V]] = None


class LRUCache(Generic[K, V]):
    """线程安全的 LRU (Least Recently Used) 缓存。

    支持 get / put 操作，时间复杂度均为 O(1)。

    实现思路：
        - 使用哈希表 (dict) 实现 O(1) 的键查找；
        - 使用双向链表维护访问顺序，头部为最近使用，尾部为最久未使用；
        - 使用 RLock 保证并发环境下的线程安全。

    Attributes:
        capacity: 缓存容量，必须为正整数。
    """

    def __init__(self, capacity: int) -> None:
        """初始化 LRU 缓存。

        Args:
            capacity: 缓存容量。

        Raises:
            ValueError: 当 capacity 不是正整数时抛出。
        """
        if not isinstance(capacity, int) or isinstance(capacity, bool):
            raise ValueError("capacity 必须是正整数")
        if capacity <= 0:
            raise ValueError("capacity 必须大于 0")

        self._capacity: int = capacity
        self._cache: dict[K, _Node[K, V]] = {}
        self._lock = threading.RLock()

        # 哨兵节点，简化边界处理
        self._head = _Node[K, V](None, None)  # type: ignore[arg-type]
        self._tail = _Node[K, V](None, None)  # type: ignore[arg-type]
        self._head.next = self._tail
        self._tail.prev = self._head

    def _remove_node(self, node: _Node[K, V]) -> None:
        """从双向链表中移除节点。"""
        prev_node = node.prev
        next_node = node.next
        if prev_node is not None:
            prev_node.next = next_node
        if next_node is not None:
            next_node.prev = prev_node

    def _add_to_head(self, node: _Node[K, V]) -> None:
        """将节点插入到链表头部（最近使用）。"""
        node.prev = self._head
        node.next = self._head.next
        if self._head.next is not None:
            self._head.next.prev = node
        self._head.next = node

    def _move_to_head(self, node: _Node[K, V]) -> None:
        """将已有节点移动到链表头部。"""
        self._remove_node(node)
        self._add_to_head(node)

    def _pop_tail(self) -> Optional[_Node[K, V]]:
        """弹出链表尾部节点（最久未使用）。"""
        tail_node = self._tail.prev
        if tail_node is None or tail_node is self._head:
            return None
        self._remove_node(tail_node)
        return tail_node

    def get(self, key: K) -> Optional[V]:
        """获取缓存中 key 对应的值。

        若 key 存在，则将其标记为最近使用并返回对应值；
        若 key 不存在，返回 None。

        Args:
            key: 缓存键。

        Returns:
            缓存值，若不存在则返回 None。
        """
        with self._lock:
            node = self._cache.get(key)
            if node is None:
                return None
            self._move_to_head(node)
            return node.value

    def put(self, key: K, value: V) -> None:
        """写入或更新缓存。

        若 key 已存在，更新其值并标记为最近使用；
        若 key 不存在，插入新节点。当缓存满时，淘汰最久未使用的条目。

        Args:
            key: 缓存键。
            value: 缓存值。
        """
        with self._lock:
            node = self._cache.get(key)
            if node is not None:
                node.value = value
                self._move_to_head(node)
                return

            new_node = _Node(key, value)
            self._cache[key] = new_node
            self._add_to_head(new_node)

            if len(self._cache) > self._capacity:
                tail_node = self._pop_tail()
                if tail_node is not None:
                    self._cache.pop(tail_node.key, None)

    def __contains__(self, key: K) -> bool:
        """判断 key 是否存在于缓存中（不改变访问顺序）。"""
        with self._lock:
            return key in self._cache

    def __len__(self) -> int:
        """返回当前缓存中的条目数。"""
        with self._lock:
            return len(self._cache)

    def clear(self) -> None:
        """清空缓存。"""
        with self._lock:
            self._cache.clear()
            self._head.next = self._tail
            self._tail.prev = self._head

    @property
    def capacity(self) -> int:
        """返回缓存容量。"""
        return self._capacity


class OrderedDictLRUCache(Generic[K, V]):
    """基于 OrderedDict 的线程安全 LRU 缓存（备选实现）。

    利用 OrderedDict 的 move_to_end 与 popitem(last=False) 实现 O(1) 操作。
    """

    def __init__(self, capacity: int) -> None:
        """初始化缓存。

        Args:
            capacity: 缓存容量。

        Raises:
            ValueError: 当 capacity 不是正整数时抛出。
        """
        if not isinstance(capacity, int) or isinstance(capacity, bool):
            raise ValueError("capacity 必须是正整数")
        if capacity <= 0:
            raise ValueError("capacity 必须大于 0")

        self._capacity: int = capacity
        self._cache: OrderedDict[K, V] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: K) -> Optional[V]:
        """获取缓存值，命中时更新访问顺序。"""
        with self._lock:
            if key not in self._cache:
                return None
            value = self._cache.pop(key)
            self._cache[key] = value
            return value

    def put(self, key: K, value: V) -> None:
        """写入缓存，满时淘汰最久未使用条目。"""
        with self._lock:
            if key in self._cache:
                self._cache.pop(key)
            self._cache[key] = value
            if len(self._cache) > self._capacity:
                self._cache.popitem(last=False)

    def __contains__(self, key: K) -> bool:
        with self._lock:
            return key in self._cache

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def capacity(self) -> int:
        return self._capacity
