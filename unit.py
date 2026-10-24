"""Install fictional addons from temporary repository layouts."""

import io
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from ashita_manager import AshitaManagerUI, BatchUpdateWorker
from folder_structure_detector import FolderStructureDetector
from package_manager import PackageManager
from package_tracker import PackageTracker


def make_files(root, paths):
    for relative_path in paths:
        file = root / relative_path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'fixture')


class AddonRepositoryLayoutTests(unittest.TestCase):
    def test_git_repository_layouts(self):
        cases = [
            ('shared monorepo', 'shared-monorepo-addon',
             ['addons/shared-monorepo-addon/shared-monorepo-addon.lua',
              'addons/other-monorepo-addon/other-monorepo-addon.lua',
              'docs/unrelated/README.md', 'README.md'], 'addons/shared-monorepo-addon'),
            ('single addon under addons', 'single-addons-folder-addon',
             ['addons/single-addons-folder-addon/single-addons-folder-addon.lua',
              'README.md'], 'addons/single-addons-folder-addon'),
            ('sibling documentation', 'sibling-docs-addon',
             ['addons/sibling-docs-addon/sibling-docs-addon.lua',
              'docs/guide.html', 'README.md'], 'addons/sibling-docs-addon'),
            ('top level addon folder', 'top-level-folder-addon',
             ['top-level-folder-addon/top-level-folder-addon.lua',
              'top-level-folder-addon/helper.lua', 'README.md'], 'top-level-folder-addon'),
            ('top level monorepo', 'top-level-monorepo-addon',
             ['other-monorepo-addon/other-monorepo-addon.lua',
              'top-level-monorepo-addon/top-level-monorepo-addon.lua',
              'third-monorepo-addon/third-monorepo-addon.lua'],
             'top-level-monorepo-addon'),
            ('root Lua with helpers', 'root-helpers-addon',
             ['root-helpers-addon.lua', 'commands.lua', 'lib/helper.lua',
              'resources/items.lua'], '.'),
            ('root Lua with bundled resources', 'bundled-resources-addon',
             ['bundled-resources-addon.lua', 'data/items.lua',
              'resources/icon.png'], '.'),
            ('mixed case entrypoint', 'mixed-case-entrypoint-addon',
             ['addons/mixed-case-entrypoint-addon/Mixed-Case-Entrypoint-Addon.lua',
              'README.md'], 'addons/mixed-case-entrypoint-addon'),
            ('nested assets', 'nested-assets-addon',
             ['nested-assets-addon/nested-assets-addon.lua',
              'nested-assets-addon/libs/color.lua',
              'nested-assets-addon/assets/icon.png', 'README.md'], 'nested-assets-addon'),
            ('single root Lua', 'single-root-lua-addon',
             ['single-root-lua-addon.lua'], '.'),
        ]

        for layout, name, paths, expected_source in cases:
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = root / 'repo'
                make_files(repo, paths)
                ashita = root / 'ashita'
                ashita.mkdir()
                tracker = PackageTracker(ashita)
                manager = PackageManager(ashita, tracker)
                source_url = f'file:///fixtures/{name}'

                detected = FolderStructureDetector().detect_addon_structure(
                    repo, name, source_url
                )
                self.assertTrue(detected['found'], detected)
                self.assertEqual(detected['path'], repo / expected_source)

                result = manager._install_addon(
                    repo, source_url, 'fixture-commit', 'main', target_name=name,
                )
                self.assertTrue(result['success'], result)
                info = tracker.get_package(name, 'addon')
                self.assertIsNotNone(info)
                self.assertTrue((ashita / info['path']).is_dir())

                if layout == 'shared monorepo':
                    self.assertFalse((ashita / 'docs/shared-monorepo-addon/unrelated').exists())
                elif layout == 'sibling documentation':
                    self.assertIn(str(Path('docs/sibling-docs-addon/guide.html')),
                                  info['doc_files'])
                elif layout == 'root Lua with bundled resources':
                    self.assertTrue((ashita / info['path'] / 'resources/icon.png').is_file())

    def test_git_update_selects_requested_addon_in_monorepo(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'repo'
            make_files(repo, ['other-monorepo-addon/other-monorepo-addon.lua',
                              'top-level-monorepo-addon/top-level-monorepo-addon.lua',
                              'third-monorepo-addon/third-monorepo-addon.lua', 'README.md'])
            ashita = root / 'ashita'
            ashita.mkdir()
            manager = PackageManager(ashita, PackageTracker(ashita))

            def git_command(command, **_kwargs):
                if command[:2] == ['git', 'clone']:
                    shutil.copytree(repo, command[-1])
                    output = ''
                elif command[:2] == ['git', 'rev-parse']:
                    output = 'main' if '--abbrev-ref' in command else 'fixture-commit'
                else:
                    output = ''
                return subprocess.CompletedProcess(command, 0, output, '')

            with patch.object(manager, '_run_command', side_effect=git_command):
                result = manager.install_from_git(
                    'file:///fixtures/top-level-monorepo', 'addon',
                    target_package_name='top-level-monorepo-addon'
                )
            self.assertTrue(result['success'], result)
            self.assertTrue((ashita / 'addons/top-level-monorepo-addon'
                                     / 'top-level-monorepo-addon.lua').is_file())
            self.assertFalse((ashita / 'addons/other-monorepo-addon').exists())

    def test_release_archive_with_wrapper_folder(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, 'w') as archive:
            archive.writestr('wrapped-release-addon/wrapped-release-addon.lua', '-- addon')
            archive.writestr('wrapped-release-addon/README.md', 'Documentation')

        class Response:
            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield archive_bytes.getvalue()

        with tempfile.TemporaryDirectory() as temporary:
            ashita = Path(temporary) / 'ashita'
            ashita.mkdir()
            manager = PackageManager(ashita, PackageTracker(ashita))
            with patch('package_manager.requests.get', return_value=Response()), \
                    patch.object(manager, '_get_release_tag', return_value='v1'):
                result = manager.install_from_release(
                    'file:///fixtures/wrapped-release-addon', 'addon',
                    asset_download_url='file:///fixtures/wrapped-release-addon.zip',
                    asset_name='wrapped-release-addon.zip',
                )
            self.assertTrue(result['success'], result)
            self.assertTrue((ashita / 'addons/wrapped-release-addon'
                                     / 'wrapped-release-addon.lua').is_file())

    def test_manual_addon_folder_with_helpers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'manual-addon-with-helpers'
            make_files(source, ['regions.lua', 'manual-addon-with-helpers.lua',
                                'font/encoding.lua'])
            ashita = root / 'ashita'
            ashita.mkdir()
            manager = PackageManager(ashita, PackageTracker(ashita))
            result = manager.manual_install_addon(
                source, expected_name='manual-addon-with-helpers'
            )
            self.assertTrue(result['success'], result)
            self.assertTrue((ashita / 'addons/manual-addon-with-helpers'
                                     / 'manual-addon-with-helpers.lua').is_file())


class ExtraFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ashita = self.root / 'ashita'
        self.ashita.mkdir()
        self.tracker = PackageTracker(self.ashita)
        self.manager = PackageManager(self.ashita, self.tracker)

    def write(self, relative_path, contents='fixture'):
        path = self.root / 'repo' / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding='utf-8')
        return path

    def test_plugin_update_tracks_repo_docs_and_nested_resources(self):
        repo = self.root / 'repo'
        self.write('plugins/PluginWithNestedResources.dll', 'new dll')
        self.write('docs/pluginwithnestedresources/readme.html', 'html doc')
        self.write('readme.md', 'repo readme')
        self.write('resources/shared/index.xml', 'index')
        self.write('resources/shared/entities/zone.xml', 'entities')
        self.write('resources/shared/pluginwithnestedresources/destination.xml', 'destinations')
        old_info = {
            'source': 'file:///fixtures/pluginwithnestedresources',
            'install_method': 'git',
            'commit': 'old',
            'branch': 'main',
            'doc_files': ['docs/PluginWithNestedResources/readme.html'],
            'resource_files': ['resources/shared/index.xml'],
        }
        self.tracker.add_package('PluginWithNestedResources', 'plugin', old_info)
        old_dll = self.ashita / 'plugins' / 'PluginWithNestedResources.dll'
        old_dll.write_text('old dll', encoding='utf-8')

        def install(_url, _pkg_type, **_kwargs):
            return self.manager._install_plugin(repo, old_info['source'], 'new', 'main', target_name='PluginWithNestedResources')

        with patch.object(self.manager, '_get_remote_commit_hash', return_value={'sha': 'new'}), \
                patch.object(self.manager, 'install_from_git', side_effect=install):
            result = self.manager.update_package('PluginWithNestedResources', 'plugin')

        self.assertTrue(result['success'], result)
        info = self.tracker.get_package('PluginWithNestedResources', 'plugin')
        self.assertEqual(info['doc_files'], [str(Path('docs/PluginWithNestedResources/readme.html'))])
        self.assertEqual(set(info['resource_files']), {
            str(Path('resources/shared/index.xml')),
            str(Path('resources/shared/entities/zone.xml')),
            str(Path('resources/shared/pluginwithnestedresources/destination.xml')),
        })
        for path in info['doc_files'] + info['resource_files']:
            self.assertTrue((self.ashita / path).is_file(), path)
        self.assertEqual(old_dll.read_text(encoding='utf-8'), 'new dll')

    def test_plugin_with_root_dll_and_single_docs_folder(self):
        repo = self.root / 'repo'
        self.write('PluginWithRootDllAndDocs.dll')
        self.write('docs/guide.html')
        result = self.manager._install_plugin(
            repo, 'file:///fixtures/PluginWithRootDllAndDocs', 'new', 'main'
        )
        self.assertTrue(result['success'], result)
        self.assertEqual(
            self.tracker.get_package('PluginWithRootDllAndDocs', 'plugin')['doc_files'],
            [str(Path('docs/PluginWithRootDllAndDocs/guide.html'))],
        )

    def test_resource_file_directly_in_resources_folder(self):
        repo = self.root / 'repo'
        self.write('plugins/PluginWithRootResourceFile.dll')
        self.write('resources/settings.xml')
        result = self.manager._install_plugin(
            repo, 'file:///fixtures/PluginWithRootResourceFile', 'new', 'main'
        )
        self.assertTrue(result['success'], result)
        self.assertEqual(
            self.tracker.get_package('PluginWithRootResourceFile', 'plugin')['resource_files'],
            [str(Path('resources/settings.xml'))],
        )

    def test_addon_keeps_package_specific_docs_and_resources(self):
        repo = self.root / 'repo'
        name = 'AddonWithPackageSpecificExtras'
        self.write(f'addons/{name}/{name}.lua')
        self.write(f'docs/{name}/guide.html')
        self.write(f'resources/{name}/settings.xml')
        result = self.manager._install_addon(
            repo, f'file:///fixtures/{name}', 'new', 'main', target_name=name
        )
        self.assertTrue(result['success'], result)
        info = self.tracker.get_package(name, 'addon')
        self.assertEqual(info['doc_files'], [str(Path('docs') / name / 'guide.html')])
        self.assertEqual(info['resource_files'],
                         [str(Path('resources') / name / 'settings.xml')])


class PackageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.ashita = Path(self.temp_dir.name)
        self.tracker = PackageTracker(self.ashita)
        self.manager = PackageManager(self.ashita, self.tracker)

    def installed_package(self, name, pkg_type='plugin', method='git'):
        relative = (Path('plugins') / f'{name}.dll' if pkg_type == 'plugin'
                    else Path('addons') / name / f'{name}.lua')
        file = self.ashita / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'original version')
        info = {
            'source': f'file:///fixtures/{name}',
            'install_method': method,
            'commit': 'old-commit',
            'path': str(relative.parent if pkg_type == 'addon' else relative),
        }
        self.tracker.add_package(name, pkg_type, info)
        return file, info.copy()

    def test_failed_update_restores_installed_file_and_tracking(self):
        for name, pkg_type in [('FailedPluginUpdate', 'plugin'),
                               ('FailedAddonUpdate', 'addon')]:
            with self.subTest(pkg_type=pkg_type):
                file, original_info = self.installed_package(name, pkg_type)
                with patch.object(self.manager, '_get_remote_commit_hash',
                                  return_value={'sha': 'new-commit'}), \
                        patch.object(self.manager, 'install_from_git',
                                     return_value={'success': False, 'error': 'install failed'}):
                    result = self.manager.update_package(name, pkg_type)
                self.assertFalse(result['success'])
                self.assertEqual(file.read_bytes(), b'original version')
                self.assertEqual(self.tracker.get_package(name, pkg_type), original_info)
                backup = (file.parent.with_name(name + '.backup') if pkg_type == 'addon'
                          else file.with_name(file.name + '.backup'))
                self.assertFalse(backup.exists())

    def test_variant_selection_keeps_installed_plugin(self):
        for method in ('git', 'release'):
            with self.subTest(method=method):
                name = f'VariantSelection{method.title()}Plugin'
                file, original_info = self.installed_package(name, method=method)
                selection = {'success': False, 'requires_variant_selection': True,
                             'variants': [{'name': 'variant-one'}]}
                installer = 'install_from_git' if method == 'git' else 'install_from_release'
                with patch.object(self.manager, '_get_remote_commit_hash',
                                  return_value={'sha': 'new-commit'}), \
                        patch.object(self.manager, installer, return_value=selection):
                    result = self.manager.update_package(name, 'plugin')
                self.assertTrue(result.get('requires_variant_selection'), result)
                self.assertEqual(file.read_bytes(), b'original version')
                self.assertEqual(self.tracker.get_package(name, 'plugin'), original_info)
                self.assertFalse(file.with_name(file.name + '.backup').exists())

    def test_remove_preserves_docs_and_resources_shared_with_another_package(self):
        first_file, _ = self.installed_package('FirstSharedFilesPlugin')
        second_file, _ = self.installed_package('SecondSharedFilesPlugin')
        shared_doc = Path('docs/FirstSharedFilesPlugin/guide.html')
        shared_resource = Path('resources/shared/data.xml')
        for relative in (shared_doc, shared_resource):
            file = self.ashita / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'shared')
        for name in ('FirstSharedFilesPlugin', 'SecondSharedFilesPlugin'):
            self.tracker.update_package_info(name, 'plugin', {
                'doc_files': [str(shared_doc)],
                'resource_files': [str(shared_resource)],
            })

        result = self.manager.remove_package('FirstSharedFilesPlugin', 'plugin')
        self.assertTrue(result['success'], result)
        self.assertFalse(first_file.exists())
        self.assertTrue(second_file.exists())
        self.assertTrue((self.ashita / shared_doc).is_file())
        self.assertTrue((self.ashita / shared_resource).is_file())
        self.assertIsNotNone(self.tracker.get_package('SecondSharedFilesPlugin', 'plugin'))

    def test_unchanged_commit_skips_reinstall_and_keeps_tracking(self):
        file, original_info = self.installed_package('UnchangedCommitPlugin')
        with patch.object(self.manager, '_get_remote_commit_hash',
                          return_value={'sha': 'old-commit'}), \
                patch.object(self.manager, 'install_from_git') as installer:
            result = self.manager.update_package('UnchangedCommitPlugin', 'plugin')
        self.assertTrue(result['already_updated'], result)
        installer.assert_not_called()
        self.assertEqual(file.read_bytes(), b'original version')
        self.assertEqual(self.tracker.get_package('UnchangedCommitPlugin', 'plugin'),
                         original_info)


class RescanTests(unittest.TestCase):
    def test_rescan_indexes_only_new_valid_packages_without_network_or_copying(self):
        with tempfile.TemporaryDirectory() as temporary:
            ashita = Path(temporary)
            tracker = PackageTracker(ashita)
            manager = PackageManager(ashita, tracker)

            existing_addon = {'source': 'file:///fixtures/existing-addon',
                              'install_method': 'git', 'commit': 'saved-commit',
                              'doc_files': ['docs/existing-addon/guide.html']}
            existing_plugin = {'source': 'file:///fixtures/existing-plugin',
                               'install_method': 'release', 'release_tag': 'v1'}
            tracker.add_package('ExistingAddon', 'addon', existing_addon.copy())
            tracker.add_package('ExistingPlugin', 'plugin', existing_plugin.copy())

            make_files(ashita, [
                'addons/existingaddon/existingaddon.lua',
                'addons/NewAddon/newaddon.lua',
                'addons/InvalidAddon/helper.lua',
                'addons/libs/libs.lua',
                'addons/SavedAddon.backup/SavedAddon.backup.lua',
                'plugins/existingplugin.dll',
                'plugins/NewPlugin.DLL',
                'plugins/NotAPlugin.dll.backup',
            ])
            new_addon = ashita / 'addons/NewAddon/newaddon.lua'
            new_plugin = ashita / 'plugins/NewPlugin.DLL'

            with patch.object(manager, '_fetch_official_repo_catalog',
                              side_effect=AssertionError('catalog lookup is unnecessary')), \
                    patch.object(manager, '_detect_git_metadata',
                                 side_effect=AssertionError('Git metadata is unnecessary')):
                first = manager.rescan_untracked_packages()
                second = manager.rescan_untracked_packages()

            self.assertEqual(first, {'addons': 1, 'plugins': 1,
                                     'addon_names': ['NewAddon'],
                                     'plugin_names': ['NewPlugin']})
            self.assertEqual(second, {'addons': 0, 'plugins': 0,
                                      'addon_names': [], 'plugin_names': []})
            self.assertEqual(tracker.get_package('ExistingAddon', 'addon'), existing_addon)
            self.assertEqual(tracker.get_package('ExistingPlugin', 'plugin'), existing_plugin)
            self.assertIsNone(tracker.get_package('InvalidAddon', 'addon'))
            self.assertIsNone(tracker.get_package('libs', 'addon'))
            self.assertIsNone(tracker.get_package('SavedAddon.backup', 'addon'))
            self.assertIsNone(tracker.get_package('NotAPlugin', 'plugin'))
            for name, pkg_type in [('NewAddon', 'addon'), ('NewPlugin', 'plugin')]:
                info = tracker.get_package(name, pkg_type)
                self.assertEqual(info['install_method'], 'manual')
                self.assertEqual(info['source'], 'unknown')
                self.assertTrue((ashita / info['path']).exists())
            self.assertEqual(new_addon.read_bytes(), b'fixture')
            self.assertEqual(new_plugin.read_bytes(), b'fixture')


class ManualBundleTests(unittest.TestCase):
    def test_manual_install_adds_every_addon_in_selected_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / 'download' / 'addons'
            docs = root / 'download' / 'docs'
            resources = root / 'download' / 'resources'
            make_files(bundle, [
                'FirstBundleAddon/FirstBundleAddon.lua',
                'FirstBundleAddon/helper.lua',
                'SecondBundleAddon/SecondBundleAddon.lua',
                'libs/shared.lua',
            ])
            make_files(docs, ['addons/firstbundleaddon/guide.html',
                              'addons/SecondBundleAddon/guide.html'])
            make_files(resources, ['FirstBundleAddon/settings.xml',
                                   'SecondBundleAddon/settings.xml'])
            ashita = root / 'ashita'
            ashita.mkdir()
            tracker = PackageTracker(ashita)
            manager = PackageManager(ashita, tracker)

            result = manager.manual_install_addon(
                bundle, docs_path=docs, resources_path=resources
            )

            self.assertTrue(result['success'], result)
            self.assertEqual(result['installed_count'], 2)
            for name in ('FirstBundleAddon', 'SecondBundleAddon'):
                info = tracker.get_package(name, 'addon')
                self.assertEqual(info['install_method'], 'manual')
                self.assertTrue((ashita / 'addons' / name / f'{name}.lua').is_file())
                self.assertEqual(info['doc_files'],
                                 [str(Path('docs') / name / 'guide.html')])
                self.assertEqual(info['resource_files'],
                                 [str(Path('resources') / name / 'settings.xml')])
            self.assertIsNone(tracker.get_package('libs', 'addon'))
            self.assertFalse((ashita / 'addons/libs').exists())

    def test_manual_bundle_preserves_an_existing_addon(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / 'download'
            make_files(bundle, ['ExistingBundleAddon/ExistingBundleAddon.lua',
                                'NewBundleAddon/NewBundleAddon.lua'])
            ashita = root / 'ashita'
            ashita.mkdir()
            old_file = ashita / 'addons/ExistingBundleAddon/ExistingBundleAddon.lua'
            old_file.parent.mkdir(parents=True)
            old_file.write_bytes(b'existing version')
            tracker = PackageTracker(ashita)
            manager = PackageManager(ashita, tracker)
            tracker.add_package('ExistingBundleAddon', 'addon',
                                {'source': 'file:///fixtures/old', 'install_method': 'git'})

            result = manager.manual_install_addon(bundle)

            self.assertTrue(result['success'], result)
            self.assertEqual(result['installed_count'], 1)
            self.assertEqual(result['failed_count'], 1)
            self.assertIn('ExistingBundleAddon', result['message'])
            self.assertEqual(old_file.read_bytes(), b'existing version')
            self.assertEqual(tracker.get_package('ExistingBundleAddon', 'addon')['source'],
                             'file:///fixtures/old')
            self.assertTrue((ashita / 'addons/NewBundleAddon/NewBundleAddon.lua').is_file())


class BatchResultSummaryTests(unittest.TestCase):
    def test_batch_worker_reports_each_outcome_and_failure_reason(self):
        manager = Mock()
        outcomes = {
            'UpdatedAddon': {'success': True},
            'CurrentAddon': {'success': True, 'already_updated': True},
            'FailedAddon': {'success': False, 'error': 'Download failed'},
            'ManualAddon': {'success': False, 'requires_manual_update': True,
                            'reason': 'Select a release asset'},
        }
        manager.update_package.side_effect = lambda name, kind: outcomes[name]
        worker = BatchUpdateWorker(manager, list(outcomes), 'addon')
        received = []
        worker.finished.connect(received.append)

        worker.run()

        self.assertEqual(received, [{
            'updated': ['UpdatedAddon'],
            'passed': ['CurrentAddon'],
            'failed': [
                {'name': 'FailedAddon', 'reason': 'Download failed'},
                {'name': 'ManualAddon',
                 'reason': 'Manual update required (Select a release asset)'},
            ],
            'not_checked': [],
            'cancelled': False,
        }])

    def test_cancelled_batch_reports_packages_not_checked(self):
        manager = Mock()
        worker = BatchUpdateWorker(manager, ['FirstAddon', 'SecondAddon'], 'addon')
        received = []
        worker.finished.connect(received.append)
        worker.cancel()

        worker.run()

        self.assertEqual(received[0]['not_checked'], ['FirstAddon', 'SecondAddon'])
        self.assertTrue(received[0]['cancelled'])
        manager.update_package.assert_not_called()

    def test_batch_completion_shows_and_logs_counts_names_and_reasons(self):
        ui = Mock()
        ui._log_summary.side_effect = lambda summary: AshitaManagerUI._log_summary(ui, summary)
        results = {
            'updated': ['UpdatedAddon'],
            'passed': ['CurrentAddon'],
            'failed': [{'name': 'FailedAddon', 'reason': 'Download failed'}],
            'not_checked': [],
            'cancelled': False,
        }

        AshitaManagerUI.batch_update_finished(ui, results)

        ui.batch_progress.close.assert_called_once()
        ui.refresh_package_lists.assert_called_once()
        title, summary = ui._show_summary_dialog.call_args.args
        self.assertEqual(title, 'Batch update complete')
        for expected in ('Updated (1):\n  UpdatedAddon',
                         'Passed, already up to date (1):\n  CurrentAddon',
                         'Failed (1):\n  FailedAddon: Download failed'):
            self.assertIn(expected, summary)
        self.assertIn('  FailedAddon: Download failed',
                      [call.args[0] for call in ui.log.call_args_list])

    def test_rescan_completion_shows_and_logs_discovered_names(self):
        ui = Mock()
        ui._log_summary.side_effect = lambda summary: AshitaManagerUI._log_summary(ui, summary)

        AshitaManagerUI.rescan_finished(ui, {
            'addons': 2, 'plugins': 1,
            'addon_names': ['FirstAddon', 'SecondAddon'],
            'plugin_names': ['NewPlugin'],
        })

        ui.scan_progress.close.assert_called_once()
        ui.refresh_package_lists.assert_called_once()
        title, summary = ui._show_summary_dialog.call_args.args
        self.assertEqual(title, 'Rescan complete')
        self.assertIn('New addons (2):\n  FirstAddon\n  SecondAddon', summary)
        self.assertIn('New plugins (1):\n  NewPlugin', summary)
        self.assertIn('  NewPlugin', [call.args[0] for call in ui.log.call_args_list])


if __name__ == '__main__':
    unittest.main(verbosity=2)
