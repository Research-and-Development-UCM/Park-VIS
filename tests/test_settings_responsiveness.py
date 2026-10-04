import ast
import asyncio
from pathlib import Path
import threading
import types
import unittest


class SettingsResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_database_write_does_not_block_requests(self):
        # Exercise the actual route body without starting camera acquisition.
        tree = ast.parse(Path("backend/app.py").read_text(encoding="utf-8"))
        route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == "update_setting")
        route.decorator_list = []
        route.args.defaults = []
        for argument in route.args.args:
            argument.annotation = None
        started = threading.Event()
        release = threading.Event()
        def write(db, key, value):
            started.set()
            release.wait(2)
            return {"key": key, "value": value}
        namespace = {
            "asyncio": asyncio,
            "auth": types.SimpleNamespace(check_permission_direct=lambda *args: True),
            "crud": types.SimpleNamespace(update_setting=write),
        }
        exec(compile(ast.Module(body=[route], type_ignores=[]), "settings_route", "exec"), namespace)
        task = asyncio.create_task(namespace["update_setting"](
            "hysteresis_occupied_threshold", types.SimpleNamespace(value="0.75"), None, None))
        try:
            # A blocked write must yield, allowing another request/coroutine to run.
            await asyncio.sleep(0.05)
            self.assertTrue(started.is_set())
            self.assertFalse(task.done(), "The database wait blocked the event loop")
        finally:
            release.set()
            result = await task
        self.assertEqual(result["value"], "0.75")


if __name__ == "__main__":
    unittest.main()
