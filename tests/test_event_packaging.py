"""Every module an event-platform Lambda imports must be in its Terraform archive (a missing file = import error in AWS)."""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEARCH = [ROOT / "lambdas", ROOT / "lambdas" / "membership", ROOT / "plugins" / "seat-upgrade" / "backend" / "lambdas"]
ARCHIVES = {
    "manage_event_platform": ("event_platform_admin.tf", "manage_event_platform"),
    "event_platform_public": ("event_platform_public.tf", "event_platform_public"),
    "event_platform_webhook": ("event_platform_public.tf", "event_platform_webhook"),
    "event_platform_expiry": ("event_platform_public.tf", "event_platform_expiry"),
}


def archive_files(tf_name, archive):
    text = (ROOT / "backend" / tf_name).read_text()
    shared = re.search(r"event_platform_shared_sources = \{(.*?)\n  \}", text, re.S)
    shared_names = re.findall(r'"([\w]+\.py)"\s*=', public_text()) if shared is None else re.findall(r'"([\w]+\.py)"\s*=', shared.group(1))
    block = re.search(rf'data "archive_file" "{archive}" \{{(.*?)\n\}}\n', text, re.S).group(1)
    names = set(re.findall(r'filename\s*=\s*"([\w]+\.py)"', block))
    if "event_platform_shared_sources" in block:
        names |= set(shared_names)
    return names


def public_text():
    return (ROOT / "backend" / "event_platform_public.tf").read_text()


def local_module_file(name):
    for base in SEARCH:
        if (base / f"{name}.py").exists():
            return base / f"{name}.py"
    return None


def imported_modules(path):
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


class PackagingTests(unittest.TestCase):
    def test_archives_contain_all_local_imports(self):
        for lam, (tf, archive) in ARCHIVES.items():
            shipped = archive_files(tf, archive)
            self.assertIn("handler.py", shipped, lam)
            seen, queue, missing = set(), [ROOT / "lambdas" / lam / "handler.py"], set()
            if lam == "manage_event_platform":
                queue.append(ROOT / "lambdas" / lam / "store.py")
            while queue:
                path = queue.pop()
                for mod in imported_modules(path):
                    if mod in seen:
                        continue
                    seen.add(mod)
                    sibling = path.parent / f"{mod}.py"
                    found = sibling if sibling.exists() and path.parent.name == lam else local_module_file(mod)
                    if not found:
                        continue  # stdlib / boto3
                    if f"{mod}.py" not in shipped:
                        missing.add(f"{mod}.py")
                    queue.append(found)
            self.assertFalse(missing, f"{lam} archive lacks {sorted(missing)}")


class IamTests(unittest.TestCase):
    """Every DynamoDB call the event stores make must be allowed by the event-platform IAM policy
    (a missing action only shows up in AWS as a generic 500)."""

    CALLS = {"get_item": "GetItem", "put_item": "PutItem", "update_item": "UpdateItem", "delete_item": "DeleteItem",
             "query": "Query", "scan": "Scan", "batch_get_item": "BatchGetItem", "batch_write_item": "BatchWriteItem",
             "transact_write_items": "PutItem"}

    def test_policy_covers_store_calls(self):
        policy = (ROOT / "backend" / "dynamodb_event_platform.tf").read_text()
        allowed = set(re.findall(r'"dynamodb:(\w+)"', policy))
        for rel in ("lambdas/event_reg_store.py", "lambdas/manage_event_platform/store.py"):
            text = (ROOT / rel).read_text()
            for call, action in self.CALLS.items():
                if re.search(rf"\.{call}\(", text):
                    self.assertIn(action, allowed, f"{rel} calls {call} but the policy lacks dynamodb:{action}")


if __name__ == "__main__":
    unittest.main()
