"""Launcher preferences, portable settings, addon snapshots, and tray controls."""
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import uuid
import zipfile

DEFAULT_PREFERENCES = {
    'launch_behavior': 'Keep open', 'news_refresh_minutes': 30,
    'animations_enabled': True, 'addon_backups_enabled': True,
    'playtime_paused': False,
}
NEWS_CHOICES = {'Manual only': 0, 'Every 15 minutes': 15, 'Every 30 minutes': 30,
                'Every hour': 60, 'Every 3 hours': 180}
BACKUP_NAMES = ('AddOns', 'AddOns_Disabled', 'AddOns_curseforge.json',
                'AddOns_curseforge_matches_v2.json')


def clean_preferences(value):
    result = dict(DEFAULT_PREFERENCES)
    if not isinstance(value, dict):
        return result
    if value.get('launch_behavior') in ('Keep open', 'Minimize', 'Minimize to tray'):
        result['launch_behavior'] = value['launch_behavior']
    if type(value.get('news_refresh_minutes')) is int and value['news_refresh_minutes'] in NEWS_CHOICES.values():
        result['news_refresh_minutes'] = value['news_refresh_minutes']
    for key in ('animations_enabled', 'addon_backups_enabled', 'playtime_paused'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    return result


def backup_directory(settings_path, version):
    slug = re.sub(r'[^a-zA-Z0-9_-]+', '_', version).strip('_') or 'game'
    suffix = hashlib.sha256(version.encode()).hexdigest()[:8]
    return Path(settings_path).parent / 'addon_backups' / f'{slug}_{suffix}'


def snapshot_addons(settings_path, version, interface):
    """Archive enabled/disabled addons and CurseForge metadata together."""
    interface = Path(interface)
    destination = backup_directory(settings_path, version)
    destination.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.zip'
    target = destination / name
    temporary = target.with_suffix('.tmp')
    present = [name for name in BACKUP_NAMES if (interface / name).exists()]
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('launcher_backup.json', json.dumps({'version': version, 'present': present}))
            for name in present:
                source = interface / name
                if source.is_dir():
                    archive.writestr(name + '/', '')
                    for path in source.rglob('*'):
                        if path.is_symlink():
                            continue
                        if path.is_file():
                            archive.write(path, path.relative_to(interface).as_posix())
                        elif path.is_dir():
                            archive.writestr(path.relative_to(interface).as_posix() + '/', '')
                else:
                    archive.write(source, name)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def restore_addons(settings_path, version, interface, backup):
    """Validate first, save the current state, then replace with rollback on failure."""
    interface = Path(interface)
    interface.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.launcher-restore-', dir=interface) as temporary:
        stage = Path(temporary)
        incoming = stage / 'incoming'
        incoming.mkdir()
        with zipfile.ZipFile(backup) as archive:
            manifest = json.loads(archive.read('launcher_backup.json'))
            if manifest.get('version') != version:
                raise ValueError('This backup belongs to a different game version.')
            present = manifest.get('present')
            if not isinstance(present, list) or any(name not in BACKUP_NAMES for name in present):
                raise ValueError('Invalid backup manifest.')
            for member in archive.infolist():
                if member.filename == 'launcher_backup.json':
                    continue
                path = Path(member.filename)
                if '\\' in member.filename or path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] not in present:
                    raise ValueError('The backup contains an unsafe path.')
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('The backup contains a symbolic link.')
                archive.extract(member, incoming)
            if any(not (incoming / name).exists() for name in present):
                raise ValueError('The backup is incomplete.')
        safety = snapshot_addons(settings_path, version, interface)
        held = stage / 'previous'
        held.mkdir()
        moved, installed = [], []
        try:
            for name in BACKUP_NAMES:
                current = interface / name
                if current.exists():
                    current.rename(held / name)
                    moved.append(name)
                if name in present:
                    (incoming / name).rename(current)
                    installed.append(name)
        except Exception:
            for name in installed:
                path = interface / name
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            for name in moved:
                (held / name).rename(interface / name)
            raise
    return safety


def backup_addon_manager(base):
    """Wrap the existing manager without changing its install or dependency logic."""
    class BackedUpAddonManager(base):
        def __init__(self, *args, **kwargs):
            self._batch_backup_done = False
            super().__init__(*args, **kwargs)

        def install_zip(self, zip_path, confirm=True):
            if self.app.preferences['addon_backups_enabled'] and not self._batch_backup_done:
                try:
                    snapshot_addons(self.app.settings_path, self.version, self.addons_dir.parent)
                except (OSError, ValueError, zipfile.BadZipFile) as error:
                    self.set_status(f'Backup failed; addons were not changed: {error}', 'error')
                    return None
                self._batch_backup_done = True
            return super().install_zip(zip_path, confirm=confirm)

        def _prepare_backup(self, action):
            if self.busy:
                self.set_status('Please wait for the current task to finish.', 'warning')
                return
            self._batch_backup_done = False
            if not self.app.preferences['addon_backups_enabled']:
                return action()
            self.busy = True
            self.set_status('Backing up addons before making changes…')
            results = queue.Queue()
            def run():
                try:
                    snapshot_addons(self.app.settings_path, self.version, self.addons_dir.parent)
                    results.put(None)
                except Exception as error:
                    results.put(error)
            threading.Thread(target=run, daemon=True).start()
            def poll():
                if self.closed or not self.window.winfo_exists():
                    return
                try:
                    error = results.get_nowait()
                except queue.Empty:
                    self.window.after(100, poll)
                    return
                self.busy = False
                if error is not None:
                    self.set_status(f'Backup failed; addons were not changed: {error}', 'error')
                    return
                self._batch_backup_done = True
                action()
            self.window.after(100, poll)

        def update_addons(self):
            self._prepare_backup(super().update_addons)

        def browse_install(self):
            self._prepare_backup(super().browse_install)

        def install_from_file(self):
            self._prepare_backup(super().install_from_file)

        def install_from_url(self):
            self._prepare_backup(super().install_from_url)
    return BackedUpAddonManager


class LauncherPreferencesMixin:
    def schedule_news_refresh(self):
        timer = getattr(self, '_news_refresh_id', None)
        if timer is not None:
            self.root.after_cancel(timer)
        minutes = self.preferences['news_refresh_minutes']
        self._news_refresh_id = self.root.after(minutes * 60000, lambda: self.load_game_news(force=True)) if minutes else None

    def apply_launcher_preferences(self):
        # Reset elapsed-time sampling when tracking is paused or resumed.
        self.last_playtime_poll = time.monotonic()
        self.art_transition_generation += 1
        self.art_transitioning = False
        self.art_text_fade = 1
        if self.slideshow_after_id is not None:
            self.root.after_cancel(self.slideshow_after_id)
            self.slideshow_after_id = None
        if self.preferences['animations_enabled'] and self.slideshow_images:
            self.slideshow_after_id = self.root.after(5000, self.advance_slideshow)
        self._background_requested = None
        self.switch_game_art(self.version.get())
        self.draw_art(type('Size', (), {'width': self.art.winfo_width(), 'height': self.art.winfo_height()})())
        self.schedule_news_refresh()
        self.update_playtime_display(time.monotonic())

    def handle_launch_behavior(self):
        behavior = self.preferences['launch_behavior']
        if behavior == 'Keep open':
            return
        if behavior == 'Minimize':
            self.root.iconify()
            return
        if getattr(self, '_tray_icon', None) is not None:
            self.root.withdraw()
            return
        try:
            import pystray
            from PIL import Image, ImageDraw
            # Bundled resources are resolved by the launcher helper when available.
            icon_path = self.tray_icon_path()
            if icon_path.is_file():
                icon_image = Image.open(icon_path).convert('RGBA')
            else:
                icon_image = Image.new('RGB', (64, 64), '#211c18')
                ImageDraw.Draw(icon_image).text((22, 24), 'W', fill='#e3c36e')
            actions = self._tray_actions = queue.Queue()
            menu = pystray.Menu(pystray.MenuItem('Show launcher', lambda *_: actions.put('show'), default=True),
                                pystray.MenuItem('Exit launcher', lambda *_: actions.put('exit')))
            platform_options = {}
            if sys.platform == 'darwin':
                import AppKit
                platform_options['darwin_nsapplication'] = AppKit.NSApplication.sharedApplication()
            icon = self._tray_icon = pystray.Icon('WoWLauncher', icon_image, 'WoW Launcher', menu, **platform_options)
            def setup(tray):
                tray.visible = True
                actions.put('ready')
            def run():
                try:
                    icon.run(setup=setup)
                except Exception as error:
                    actions.put(('error', str(error)))
            if sys.platform == 'darwin':
                icon.run_detached(setup=setup)
            else:
                threading.Thread(target=run, daemon=True).start()
            deadline = time.monotonic() + 5
            def poll():
                if self._closing:
                    return
                while True:
                    try:
                        action = actions.get_nowait()
                    except queue.Empty:
                        break
                    if action == 'ready':
                        self._tray_ready = True
                        self.root.withdraw()
                    elif action == 'show':
                        self.root.deiconify(); self.root.lift()
                    elif action == 'exit':
                        self.close_launcher()
                        return
                    elif isinstance(action, tuple):
                        self.stop_tray()
                        self.root.deiconify(); self.root.iconify()
                        self.set_status('Tray unavailable; minimized to the taskbar: ' + action[1])
                        return
                if not getattr(self, '_tray_ready', False) and time.monotonic() > deadline:
                    self.stop_tray()
                    self.root.iconify()
                    self.set_status('Tray unavailable; minimized to the taskbar.')
                    return
                self.root.after(150, poll)
            poll()
        except Exception as error:
            self.stop_tray()
            self.root.iconify()
            self.set_status(f'Tray unavailable; minimized normally. Install pystray to enable tray support. {error}')

    def stop_tray(self):
        icon = getattr(self, '_tray_icon', None)
        self._tray_icon = None
        self._tray_ready = False
        if icon is not None:
            icon.stop()

    def open_preferences(self, button_class, overlay_class, dropdown_class, versions):
        window = overlay_class(self.root, 900, 660)
        body = window.make_body()
        theme = self.theme
        panel = theme['panel']
        status = tk.StringVar(value='Preferences are saved on this computer. Data actions apply immediately.')
        controls = {key: (tk.BooleanVar(value=value) if isinstance(value, bool) else tk.StringVar(value=str(value)))
                    for key, value in self.preferences.items()}
        default_game = tk.StringVar(value=self.preferred_version if self.preferred_version in versions else versions[0])
        pages = {}
        def button(parent, text, command):
            return button_class(parent, text=text, command=command, font=self.ui_font(9, bold=True),
                padx=10, pady=5, bg=panel, theme_provider=lambda: self.theme, style='subtle')
        def label(parent, text, size=10, color='text'):
            widget = tk.Label(parent, text=text, bg=panel, fg=theme[color], font=self.ui_font(size),
                              wraplength=800, justify='left', anchor='w')
            widget.pack(fill='x', pady=(0, 8))
            return widget
        footer = tk.Frame(body, bg=panel)
        footer.pack(side='bottom', fill='x', pady=(12, 0))
        tk.Label(body, textvariable=status, bg=panel, fg=theme['muted'], font=self.ui_font(9),
                 wraplength=800, justify='left').pack(side='bottom', fill='x', pady=(8, 0))
        label(body, 'OPTIONS', 18, 'accent')
        tabs = tk.Frame(body, bg=panel); tabs.pack(fill='x', pady=(0, 12))
        content = tk.Frame(body, bg=panel); content.pack(fill='both', expand=True)
        def select(name):
            for page in pages.values(): page.pack_forget()
            pages[name].pack(fill='both', expand=True)
        for name in ('General', 'Games', 'Addons', 'Data'):
            pages[name] = tk.Frame(content, bg=panel)
            button(tabs, name.upper(), lambda name=name: select(name)).pack(side='left', padx=(0, 8))
        def dropdown(parent, caption, variable, values):
            label(parent, caption, 10, 'accent')
            menu = dropdown_class(parent, variable, *values)
            menu.configure(font=self.ui_font(10), bg=theme['control'], fg=theme['text'])
            menu.pack(fill='x', pady=(0, 14))
            return menu
        def checkbox(parent, caption, key):
            tk.Checkbutton(parent, text=caption, variable=controls[key], bg=panel, fg=theme['text'],
                activebackground=panel, activeforeground=theme['accent'], selectcolor=theme['control'],
                font=self.ui_font(11), anchor='w').pack(fill='x', pady=6)
        general = pages['General']
        dropdown(general, 'DEFAULT GAME AT STARTUP', default_game, versions)
        dropdown(general, 'WHEN A GAME LAUNCHES', controls['launch_behavior'], ('Keep open', 'Minimize', 'Minimize to tray'))
        news_choice = tk.StringVar(value=next(name for name, value in NEWS_CHOICES.items() if value == self.preferences['news_refresh_minutes']))
        dropdown(general, 'NEWS REFRESH', news_choice, tuple(NEWS_CHOICES))
        label(general, 'Manual only keeps the Refresh button available. News still loads when you open or switch games.', 9, 'muted')
        checkbox(general, 'Animate backgrounds and rotate screenshots', 'animations_enabled')
        checkbox(general, 'Pause playtime tracking', 'playtime_paused')
        label(general, 'Minimize to tray requires pystray. If the tray is unavailable, the launcher minimizes normally.', 9, 'muted')
        def leave_and_open(callback):
            window.destroy(); self.root.after(50, callback)
        games = pages['Games']
        label(games, 'GAME INSTALLATIONS', 14, 'accent')
        label(games, 'Manage each game folder, launch arguments, additional clients, and game configuration files.')
        button(games, 'GAME LOCATIONS AND LAUNCH ARGUMENTS', lambda: leave_and_open(self.open_game_options)).pack(anchor='w', pady=8)
        button(games, 'SHOW WELCOME / RUN SETUP AGAIN', lambda: leave_and_open(self.open_setup_wizard)).pack(anchor='w', pady=8)
        label(games, 'Choose a game folder, not its executable. READY means a compatible executable was found.', 10, 'muted')
        addons = pages['Addons']
        checkbox(addons, 'Back up addons before installs and updates', 'addon_backups_enabled')
        label(addons, 'Snapshots include enabled and disabled addons and CurseForge metadata. Each install/update batch gets one backup. Restoring also backs up your current addons.')
        backup_game = tk.StringVar(value=self.version.get())
        dropdown(addons, 'GAME FOR BACKUP / RESTORE', backup_game, versions)
        tasks = queue.Queue(); busy = {'value': False}
        def work(description, operation):
            if busy['value']:
                status.set('Wait for the current file operation to finish.'); return
            busy['value'] = True; status.set(description)
            def run():
                try: tasks.put((True, str(operation())))
                except Exception as error: tasks.put((False, str(error)))
            threading.Thread(target=run, daemon=True).start()
            def poll():
                if not window.winfo_exists(): return
                try: ok, result = tasks.get_nowait()
                except queue.Empty:
                    window.after(100, poll); return
                busy['value'] = False
                status.set(('Done: ' if ok else 'Failed: ') + result)
            window.after(100, poll)
        def interface_for(version):
            path = self.game_paths.get(version, '')
            executable = self.find_executable(version, path) if path else None
            if executable is None: raise ValueError('Set a valid installation folder for this game first.')
            return executable.parent / 'Interface'
        def make_backup():
            version = backup_game.get()
            try: interface = interface_for(version)
            except ValueError as error: status.set(str(error)); return
            work('Backing up addons…', lambda: snapshot_addons(self.settings_path, version, interface))
        def restore_backup():
            version = backup_game.get()
            try: interface = interface_for(version)
            except ValueError as error: status.set(str(error)); return
            directory = backup_directory(self.settings_path, version); directory.mkdir(parents=True, exist_ok=True)
            path = filedialog.askopenfilename(parent=window, title='Choose an addon backup', initialdir=str(directory), filetypes=[('Addon backups', '*.zip')])
            if path and messagebox.askyesno('Restore addons', 'Replace this game’s current addon files with this backup? A safety backup will be created first. Close the game before restoring.', parent=window):
                work('Restoring addons…', lambda: 'Restored. Safety backup: ' + str(restore_addons(self.settings_path, version, interface, path)))
        for caption, callback in (('BACK UP NOW', make_backup), ('RESTORE BACKUP', restore_backup),
            ('OPEN BACKUP FOLDER', lambda: self.open_directory(self.ensure_backup_folder(backup_game.get()), 'addon backups'))):
            button(addons, caption, callback).pack(anchor='w', pady=5)
        data = pages['Data']
        data_game = tk.StringVar(value=self.version.get())
        dropdown(data, 'GAME FOR PLAYTIME RESET', data_game, versions)
        row = tk.Frame(data, bg=panel); row.pack(fill='x', pady=6)
        button(row, 'EXPORT PLAYTIME CSV', lambda: self.export_playtime(window, status)).pack(side='left', padx=(0, 8))
        def reset_playtime():
            version = data_game.get()
            if messagebox.askyesno('Reset playtime', f'Delete all recorded playtime for {version}?', parent=window):
                self.last_playtime_poll = time.monotonic()
                self.playtime_seconds[version] = 0.0
                for values in self.playtime_history.values(): values.pop(version, None)
                self.persist_settings(); self.update_playtime_display(time.monotonic()); status.set(f'Playtime reset for {version}.')
        button(row, 'RESET GAME PLAYTIME', reset_playtime).pack(side='left')
        def reset_tips():
            self.onboarding_hints_seen.clear(); self.persist_settings(); status.set('First-use tips will appear again when you open each section.')
        button(data, 'SHOW TIPS AGAIN', reset_tips).pack(anchor='w', pady=6)
        row = tk.Frame(data, bg=panel); row.pack(fill='x', pady=6)
        button(row, 'EXPORT SETTINGS', lambda: self.export_launcher_settings(window, status)).pack(side='left', padx=(0, 8))
        button(row, 'IMPORT SETTINGS', lambda: self.import_launcher_settings(window, status)).pack(side='left')
        button(data, 'OPEN SETTINGS FOLDER', lambda: self.open_directory(self.settings_path.parent, 'launcher settings')).pack(anchor='w', pady=6)
        keep_time = tk.BooleanVar(value=True); keep_characters = tk.BooleanVar(value=True)
        for text, variable in (('Keep playtime when resetting settings', keep_time), ('Keep saved characters when resetting settings', keep_characters)):
            tk.Checkbutton(data, text=text, variable=variable, bg=panel, fg=theme['text'], selectcolor=theme['control'],
                activebackground=panel, activeforeground=theme['accent'], font=self.ui_font(10)).pack(anchor='w', pady=3)
        button(data, 'RESET SETTINGS', lambda: self.reset_launcher_settings(window, status, keep_time.get(), keep_characters.get())).pack(anchor='w', pady=6)
        def save():
            self.preferences = clean_preferences({key: variable.get() for key, variable in controls.items() if key != 'news_refresh_minutes'})
            self.preferences['news_refresh_minutes'] = NEWS_CHOICES[news_choice.get()]
            self.preferred_version = default_game.get()
            if self.persist_settings():
                self.apply_launcher_preferences(); window.destroy()
            else: status.set('Settings could not be saved.')
        button(footer, 'SAVE', save).pack(side='right')
        button(footer, 'CANCEL', window.destroy).pack(side='right', padx=(0, 8))
        select('General')
        self.apply_theme(self.version.get(), subtree=window)

    def ensure_backup_folder(self, version):
        folder = backup_directory(self.settings_path, version); folder.mkdir(parents=True, exist_ok=True)
        return folder

    def export_playtime(self, parent, status):
        path = filedialog.asksaveasfilename(parent=parent, title='Export playtime', defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path: return
        try:
            with open(path, 'w', newline='', encoding='utf-8') as stream:
                writer = csv.writer(stream); writer.writerow(['record', 'date', 'game', 'seconds', 'hours'])
                for game, seconds in sorted(self.playtime_seconds.items()): writer.writerow(['lifetime', '', game, seconds, round(seconds / 3600, 4)])
                for day, values in sorted(self.playtime_history.items()):
                    for game, seconds in sorted(values.items()): writer.writerow(['daily', day, game, seconds, round(seconds / 3600, 4)])
            status.set('Playtime exported.')
        except OSError as error: status.set(f'Export failed: {error}')

    def export_launcher_settings(self, parent, status):
        path = filedialog.asksaveasfilename(parent=parent, title='Export saved launcher settings', defaultextension='.json', filetypes=[('JSON', '*.json')])
        if not path: return
        if not self.persist_settings(): status.set('Could not save settings for export.'); return
        try:
            shutil.copyfile(self.settings_path, path); status.set('Saved settings exported. Local game paths may need updating on another computer.')
        except (OSError, shutil.SameFileError) as error: status.set(f'Export failed: {error}')

    def import_launcher_settings(self, parent, status):
        path = filedialog.askopenfilename(parent=parent, title='Import launcher settings', filetypes=[('JSON', '*.json')])
        if not path: return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or not isinstance(data.get('game_paths'), dict): raise ValueError('This is not a launcher settings file.')
            if any(not isinstance(key, str) or not isinstance(value, str) for key, value in data['game_paths'].items()): raise ValueError('Invalid game paths.')
            if not messagebox.askyesno('Import settings', 'Replace saved settings with this file? Current settings will be backed up. Restart the launcher afterwards to apply imported settings.', parent=parent): return
            data['preferences'] = clean_preferences(data.get('preferences'))
            self.replace_stored_settings(data)
            self.game_paths = self.load_settings()
            self.last_playtime_poll = time.monotonic()
            self.apply_setup_results(); self.apply_launcher_preferences()
            parent.destroy()
            messagebox.showinfo('Settings imported', 'Settings imported. Restart the launcher to refresh all saved character and game views.', parent=self.root)
        except (OSError, ValueError, TypeError, AttributeError) as error: status.set(f'Import failed: {error}')

    def replace_stored_settings(self, data):
        # Keep a recoverable copy; write the replacement atomically.
        parent = self.settings_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        if self.settings_path.exists():
            shutil.copy2(self.settings_path, parent / ('settings-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.json'))
        staged = self.settings_path.with_suffix('.import.tmp')
        staged.write_text(json.dumps(data, indent=2), encoding='utf-8'); staged.replace(self.settings_path)

    def reset_launcher_settings(self, parent, status, keep_time, keep_characters):
        if not messagebox.askyesno('Reset launcher settings', 'Reset preferences, game paths, launch arguments, and tips? The selected playtime and character data will be kept. Addon files and backups are preserved.', parent=parent): return
        data = {'game_paths': {}, 'setup_complete': False, 'preferences': dict(DEFAULT_PREFERENCES)}
        if keep_time:
            data.update(playtime_seconds=self.playtime_seconds, playtime_history=self.playtime_history, playtime_history_started=self.playtime_history_started)
        if keep_characters: data['armory'] = self.armory
        try:
            self.replace_stored_settings(data)
            self.game_paths = self.load_settings()
            self.last_playtime_poll = time.monotonic()
            self.apply_setup_results(); self.apply_launcher_preferences()
            parent.destroy(); self.root.after(50, self.open_setup_wizard)
        except (OSError, ValueError) as error: status.set(f'Reset failed: {error}')
