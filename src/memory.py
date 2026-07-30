import json
from pathlib import Path

class LongTermMemoryStore:
    def __init__(self, filename: str = "long_term_store.json"):
        self.filepath = Path(filename)
        self.memory = self._load_store()

    def _load_store(self) -> dict:
        if self.filepath.exists():
            try:
                with open(self.filepath, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def save_memory(self, namespace: str, key: str, value: dict):
        self.memory.setdefault(namespace, {})[key] = value
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(self.filepath, "w") as f:
            json.dump(self.memory, f, indent=4)

    def get_memory(self, namespace: str, key: str) -> dict:
        return self.memory.get(namespace, {}).get(key, {})

    def get_all_context(self, namespace: str) -> str:
        records = self.memory.get(namespace, {})
        if not records:
            return "No prior review history stored for this namespace."
        return json.dumps(records, indent=2)
