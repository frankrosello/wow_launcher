"""Nonblocking launcher release checks; installs remain under the user's control."""
import json
import queue
import re
import threading
import webbrowser

from packaging.version import Version
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from tkinter import messagebox

LAUNCHER_VERSION = '1.0.1'
RELEASES_URL = 'https://github.com/frankrosello/wow_launcher/releases'
LATEST_API = 'https://api.github.com/repos/frankrosello/wow_launcher/releases/latest'


def version_key(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:\+[0-9A-Za-z.-]+)?', str(value).strip())
    if not match:
        raise ValueError('Release version must use a stable vMAJOR.MINOR.PATCH tag.')
    return Version(".".join(match.groups()))


def fetch_latest_release():
    request = Request(LATEST_API, headers={'Accept': 'application/vnd.github+json',
                                         'User-Agent': 'WoWLauncher/' + LAUNCHER_VERSION})
    try:
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read(1024 * 1024))
    except HTTPError as error:
        if error.code == 404:
            return None
        if error.code == 403:
            raise RuntimeError('GitHub temporarily limited update checks. Try again later.') from error
        raise
    if data.get('draft') or data.get('prerelease'):
        return None
    tag = data['tag_name']
    version_key(tag)
    url = data.get('html_url', '')
    if not url.startswith(RELEASES_URL + '/tag/'):
        raise ValueError('Unexpected release link returned by GitHub.')
    return {'tag': tag, 'url': url}


class LauncherUpdateController:
    def __init__(self, app):
        self.app = app
        self.busy = False
        self.manual = False
        self.latest = None
        self.results = queue.Queue()

    def check(self, manual=False):
        self.manual = self.manual or manual
        if self.busy:
            return
        self.busy = True
        if manual:
            self.app.set_status('Checking for launcher updates…')
        def run():
            try:
                self.results.put((fetch_latest_release(), None))
            except Exception as error:
                self.results.put((None, str(error)))
        threading.Thread(target=run, daemon=True).start()
        self.app.root.after(100, self._poll)

    def _poll(self):
        if getattr(self.app, '_closing', False):
            return
        try:
            release, error = self.results.get_nowait()
        except queue.Empty:
            self.app.root.after(100, self._poll)
            return
        manual, self.manual, self.busy = self.manual, False, False
        if error:
            if manual:
                self.app.set_status('Could not check launcher updates.')
                messagebox.showinfo('Launcher updates', 'Could not check for updates.\n\n' + error,
                                    parent=self.app.root)
            return
        self.latest = release
        newer = release is not None and version_key(release['tag']) > version_key(LAUNCHER_VERSION)
        button = self.app.launcher_update_button
        if newer:
            button.set_label('LAUNCHER UPDATE ' + release['tag'])
            button.place(relx=.96, rely=.115, anchor='ne')
            self.app.set_status('Launcher ' + release['tag'] + ' is available.')
            if manual:
                self.open_release()
        else:
            button.place_forget()
            if manual:
                message = ('Launcher ' + LAUNCHER_VERSION + ' is up to date.' if release else
                           'No published stable launcher release was found.')
                self.app.set_status(message)
                messagebox.showinfo('Launcher updates', message, parent=self.app.root)

    def open_release(self):
        if not self.latest:
            return
        if messagebox.askyesno('Launcher update available',
                'Installed: ' + LAUNCHER_VERSION + '\nAvailable: ' + self.latest['tag'] +
                '\n\nOpen the release page to read the changes and download the build for your system?',
                parent=self.app.root):
            webbrowser.open(self.latest['url'])
