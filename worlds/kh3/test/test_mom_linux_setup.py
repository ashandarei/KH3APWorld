import asyncio
import json
import os
import shlex
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from ..client_support import mom_installation as installer
from ..client_support import mom_linux_setup as setup


class TestMoMLinuxSetup(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.game = self.home / 'games/MoM with spaces'
        self.game.mkdir(parents=True)
        for name in (setup.MOM_EXE, 'GameAssembly.dll'):
            (self.game / name).write_bytes(b'fixture')
        self.heroic = self.home / '.config/heroic'
        self.database = self.heroic / 'legendaryConfig/legendary'
        self.runner = self.home / 'wine'
        self.runner.write_bytes(b'fixture')
        self.runner.chmod(0o700)
        self.prefix = self.home / 'prefix'
        self.prefix.mkdir()
        self.write(self.database / 'installed.json', {'MoMApp': {'install_path': str(self.game)}})
        self.write(self.heroic / 'config.json', {'defaultSettings': {
            'wineVersion': {'type': 'wine', 'bin': str(self.runner)}, 'winePrefix': str(self.prefix)}})
        self.write(self.heroic / 'GamesConfig/MoMApp.json', {'version': 'v0.1', 'MoMApp': {'enableEsync': False}})
        for mock in (patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.home / '.config')}, clear=True),
                     patch.object(Path, 'home', return_value=self.home),
                     patch.object(setup.shutil, 'which', return_value=str(self.runner))):
            mock.start()
            self.addCleanup(mock.stop)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def response(self, **overrides):
        return {**dict(game_directory=str(self.game), game_executable=setup.MOM_EXE,
                      launch_command=[str(self.runner)], game_parameters=['-example'],
                      user_parameters=[], egl_parameters=['-AUTH_PASSWORD=transient-token'],
                      environment={}, working_directory=str(self.game)), **overrides}

    def proton_settings(self, **overrides):
        proton = self.home / 'GE Proton/proton'
        proton.parent.mkdir(exist_ok=True)
        proton.write_bytes(b'fixture')
        proton.chmod(0o700)
        self.write(self.heroic / 'GamesConfig/MoMApp.json', {'MoMApp': {
            'wineVersion': {'type': 'proton', 'bin': str(proton)},
            'enableEsync': False, 'enableFsync': True, **overrides}})
        return proton

    def bundled_umu(self):
        umu = self.heroic / 'tools/runtimes/umu/umu_run.py'
        umu.parent.mkdir(parents=True)
        umu.write_bytes(b'fixture')
        umu.chmod(0o700)
        return umu

    def test_proton_umu_uses_heroic_runtime_prefix_and_cached_game_id(self):
        proton = self.proton_settings()
        umu = self.bundled_umu()
        self.write(self.heroic / 'store_cache/umu.json', {'legendary_MoMApp': 'umu-mom'})
        with patch.object(setup.shutil, 'which', side_effect=lambda name: str(self.runner) if name == 'legendary' else None):
            recipe = setup.recipe_for(self.game)
        self.assertEqual(recipe['runner'], str(umu))
        self.assertEqual(shlex.split(recipe['query'][recipe['query'].index('--wrapper') + 1]), [str(umu)])
        self.assertIn('--no-wine', recipe['query'])
        env = recipe['environment']
        self.assertEqual(env['PROTONPATH'], str(proton.parent))
        self.assertEqual(env['WINEPREFIX'], str(self.prefix))
        self.assertEqual(env['STEAM_COMPAT_DATA_PATH'], str(self.prefix))
        self.assertEqual(env['PROTON_VERB'], 'waitforexitandrun')
        self.assertEqual(env['GAMEID'], 'umu-mom')
        self.assertEqual(env['STORE'], 'egs')
        self.assertEqual(env['PROTON_NO_ESYNC'], '1')
        self.assertEqual(env['PROTON_NO_FSYNC'], '0')
        self.assertIn('version=n,b', env['WINEDLLOVERRIDES'])
        # A hostile/mismatched launch recipe cannot replace the selected prefix.
        response = self.response(launch_command=[str(umu)], environment={'WINEPREFIX': '/wrong'})
        with patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response))):
            argv, environment, cwd = setup.resolve_launch(recipe)
        self.assertEqual(argv[:2], [str(umu), str(self.game / setup.MOM_EXE)])
        self.assertIn('-AUTH_PASSWORD=transient-token', argv)
        self.assertEqual(environment['WINEPREFIX'], str(self.prefix))
        self.assertEqual(cwd, self.game)
        self.assertNotIn('transient-token', json.dumps(recipe))

    def test_proton_umu_prefers_host_launcher_and_heroic_default_game_id(self):
        self.proton_settings()
        self.bundled_umu()
        host_umu = self.home / 'host umu-run'
        host_umu.write_bytes(b'fixture')
        host_umu.chmod(0o700)
        with patch.object(setup.shutil, 'which', side_effect=lambda name: str(host_umu if name == 'umu-run' else self.runner)):
            recipe = setup.recipe_for(self.game)
        self.assertEqual(recipe['runner'], str(host_umu))
        self.assertEqual(recipe['environment']['GAMEID'], 'umu-0')

    def test_missing_umu_does_not_install_or_save(self):
        self.proton_settings()
        with patch.object(setup.shutil, 'which', side_effect=lambda name: str(self.runner) if name == 'legendary' else None), \
                patch.object(installer, 'sys', SimpleNamespace(platform='linux')), \
                patch.object(installer, 'install') as install:
            with self.assertRaisesRegex(RuntimeError, 'Launch MoM once through Heroic'):
                installer.install_integration(self.game, self.home)
        install.assert_not_called()
        self.assertFalse(setup.settings_path().exists())

    def test_explicitly_disabled_umu_keeps_direct_proton(self):
        proton = self.proton_settings(disableUMU=True)
        (self.home / '.steam/steam').mkdir(parents=True)
        recipe = setup.recipe_for(self.game)
        wrapper = recipe['query'][recipe['query'].index('--wrapper') + 1]
        self.assertEqual(shlex.split(wrapper), [str(proton), 'waitforexitandrun'])
        self.assertEqual(recipe['runner'], str(proton))
        self.assertNotIn('GAMEID', recipe['environment'])

    def test_umu_install_saves_valid_setup_without_starting_game(self):
        self.proton_settings()
        umu = self.bundled_umu()
        kh3 = self.home / 'KH3'
        win64 = kh3 / 'Binaries/Win64'
        win64.mkdir(parents=True)
        (win64 / 'KINGDOM HEARTS III.exe').write_bytes(b'fixture')
        response = self.response(launch_command=[str(umu)])
        with patch.object(setup.shutil, 'which', side_effect=lambda name: str(self.runner) if name == 'legendary' else None), \
                patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response))) as query, \
                patch.object(setup.subprocess, 'Popen') as launch, \
                patch.object(installer, 'sys', SimpleNamespace(platform='linux')), \
                patch.object(installer, 'install') as install:
            installer.install_integration(self.game, kh3)
        query.assert_called_once()
        self.assertIn('--json', query.call_args.args[0])
        launch.assert_not_called()
        install.assert_called_once()
        saved = setup.read_json(setup.settings_path())
        self.assertEqual(saved['runner'], str(umu))
        self.assertEqual(saved['token'], setup.read_json(win64 / 'kh3ap_mom_activity.json')['token'])
        self.assertNotIn('transient-token', setup.settings_path().read_text())

    def test_umu_rejects_recipe_using_different_runner(self):
        self.proton_settings()
        self.bundled_umu()
        with patch.object(setup.shutil, 'which', side_effect=lambda name: str(self.runner) if name == 'legendary' else None):
            recipe = setup.recipe_for(self.game)
        with patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.response()))):
            with self.assertRaisesRegex(RuntimeError, 'unsupported launch configuration'):
                setup.resolve_launch(recipe)

    def test_discovery_and_global_runner_with_game_overrides(self):
        self.assertEqual(len(setup.discover()), 1)
        recipe = setup.recipe_for(self.game)
        self.assertIn('--json', recipe['query'])
        self.assertNotIn('--config-path', recipe['query'])
        self.assertEqual(recipe['environment']['LEGENDARY_CONFIG_PATH'], str(self.database))
        self.assertIn(str(self.prefix), recipe['query'])
        self.assertEqual(recipe['environment']['WINEESYNC'], '0')
        self.assertIn('version=n,b', recipe['environment']['WINEDLLOVERRIDES'])

    def test_flatpak_launch_owns_runner_and_preserves_gamemode_and_environment(self):
        self.proton_settings(useGameMode=True)
        umu = self.bundled_umu()
        row = {**setup.discover()[0], 'flatpak': True}
        with patch.object(setup, 'discover', return_value=[row]), \
                patch.object(setup.shutil, 'which', side_effect=lambda name: str(self.runner) if name == 'flatpak' else None):
            recipe = setup.recipe_for(self.game)
        self.assertEqual(recipe['query'][:2], [str(self.runner), 'run'])
        self.assertIn('--env=LEGENDARY_CONFIG_PATH=' + str(self.database), recipe['query'])
        self.assertNotIn('--config-path', recipe['query'])
        self.assertIn(setup.HEROIC_APP, recipe['query'])
        self.assertEqual(recipe['runner'], str(umu))
        response = self.response(launch_command=[str(umu)])
        with patch.dict(os.environ, {'LD_LIBRARY_PATH': '/appimage-libs', 'WINEPREFIX': '/wrong'}), \
                patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response))):
            argv, environment, cwd = setup.resolve_launch(recipe)
        self.assertEqual(argv[:2], [str(self.runner), 'run'])
        self.assertIn('--command=/app/bin/gamemoderun', argv)
        self.assertEqual(argv[argv.index(setup.HEROIC_APP) + 1:][:2], [str(umu), str(self.game / setup.MOM_EXE)])
        self.assertIn('--env=WINEDLLOVERRIDES=version=n,b', argv)
        self.assertIn('--filesystem=' + str(self.prefix), argv)
        self.assertNotIn('LD_LIBRARY_PATH', environment)
        self.assertEqual(environment['WINEPREFIX'], str(self.prefix))
        self.assertNotIn('transient-token', json.dumps(recipe))

    def test_flatpak_requires_host_launcher(self):
        row = {**setup.discover()[0], 'flatpak': True}
        with patch.object(setup, 'discover', return_value=[row]), \
                patch.object(setup.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'flatpak launcher'):
                setup.recipe_for(self.game)

    def test_ambiguous_registration_and_custom_wrapper_rejected(self):
        choices = setup.discover()
        with patch.object(setup, 'discover', return_value=choices * 2):
            with self.assertRaisesRegex(RuntimeError, 'one Heroic'):
                setup.recipe_for(self.game)
        self.write(self.heroic / 'GamesConfig/MoMApp.json', {'MoMApp': {'wrapperOptions': [{'exe': 'custom'}]}})
        with self.assertRaisesRegex(RuntimeError, 'wrappers'):
            setup.recipe_for(self.game)

    def test_launch_arguments_preserved_without_persisting_tokens(self):
        recipe = setup.recipe_for(self.game)
        with patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.response()))):
            argv, environment, cwd = setup.resolve_launch(recipe)
        self.assertEqual(argv[1], str(self.game / setup.MOM_EXE))
        self.assertIn('-AUTH_PASSWORD=transient-token', argv)
        self.assertNotIn('transient-token', json.dumps(recipe))
        self.assertEqual(cwd, self.game)

    def test_wrong_game_and_failed_query_do_not_expose_tokens(self):
        recipe = setup.recipe_for(self.game)
        for response in (self.response(game_executable='another.exe'), self.response(pre_launch_command='custom')):
            with patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response))):
                with self.assertRaises(RuntimeError) as error:
                    setup.resolve_launch(recipe)
                self.assertNotIn('transient-token', str(error.exception))

    def test_setup_persists_matching_connection_and_is_repeatable(self):
        kh3 = self.home / 'KH3'
        win64 = kh3 / 'Binaries/Win64'
        win64.mkdir(parents=True)
        (win64 / 'KINGDOM HEARTS III.exe').write_bytes(b'fixture')
        with patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.response()))), \
                patch.object(installer, 'sys', SimpleNamespace(platform='linux')), \
                patch.object(installer, 'install') as install:
            message = installer.install_integration(self.game, kh3)
            self.assertIn('Restart KH3', message)
            first = setup.read_json(setup.settings_path())
            installer.install_integration(self.game, kh3)
            second = setup.read_json(setup.settings_path())
        self.assertEqual(first, second)
        self.assertEqual(install.call_count, 2)
        probe = setup.read_json(Path(first['probe_config']))
        self.assertEqual(probe['port'], first['port'])
        self.assertEqual(probe['token'], first['token'])
        self.assertNotIn('transient-token', setup.settings_path().read_text())

    def test_failed_validation_does_not_install_or_save(self):
        with patch.object(setup, 'recipe_for', side_effect=RuntimeError('unsupported')), \
                patch.object(installer, 'sys', SimpleNamespace(platform='linux')), \
                patch.object(installer, 'install') as install:
            with self.assertRaises(RuntimeError):
                installer.install_integration(self.game, self.home)
        install.assert_not_called()
        self.assertFalse(setup.settings_path().exists())

    def test_windows_install_keeps_existing_installer(self):
        progress = Mock()
        with patch.object(installer, 'sys', SimpleNamespace(platform='win32')), \
                patch.object(installer, 'install', return_value='Installed.') as install, \
                patch.object(setup, 'setup') as linux_setup:
            self.assertEqual(installer.install_integration(self.game, self.home, progress), 'Installed.')
        install.assert_called_once_with(self.game, progress)
        linux_setup.assert_not_called()

    def test_native_install_action_saves_linux_setup_and_shows_restart(self):
        from ..gui.config import ConfigView

        kh3 = self.home / 'KH3'
        win64 = kh3 / 'Binaries/Win64'
        win64.mkdir(parents=True)
        (win64 / 'KINGDOM HEARTS III.exe').write_bytes(b'fixture')

        async def work(operation):
            return await asyncio.to_thread(operation)

        view = SimpleNamespace(
            apply=AsyncMock(return_value=True), work=work,
            paths={'MoM Dir': SimpleNamespace(get=lambda: str(self.game))},
            ctx=SimpleNamespace(kh3_dir=kh3), app=SimpleNamespace(messages=Mock()))
        with patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.response()))), \
                patch.object(installer, 'sys', SimpleNamespace(platform='linux')), \
                patch.object(installer, 'install') as install:
            asyncio.run(ConfigView.mom_action(view, 'install'))
        install.assert_called_once()
        saved = setup.read_json(setup.settings_path())
        self.assertEqual(saved['game_dir'], str(self.game))
        self.assertEqual(saved['token'], setup.read_json(win64 / 'kh3ap_mom_activity.json')['token'])
        self.assertIn('Restart KH3', view.app.messages.put.call_args.args[0])
        view.apply.assert_any_await('KH3 Dir', offer_patch=False)
