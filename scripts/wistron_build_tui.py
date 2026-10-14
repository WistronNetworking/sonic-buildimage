#!/usr/bin/env python3
"""Arrow-key presentation for the SONiC build menu; no build logic lives here."""

import curses
import json
import os
import re
import sys
import threading
import time
import unicodedata


ENTER = (10, 13, curses.KEY_ENTER)
BACK = (27,)
OPTIONS = ("architecture", "customer", "clean rebuild", "build job", "build version",
           "start build", "last result", "exit")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
BRAND_RGB = {"blue": (11, 104, 148), "green": (138, 223, 76)}
BANNER_STYLES = {'classic': 'Current', 'tech': 'Tech Console',
                 'brand': 'Brand Showcase', 'retro': 'Retro Terminal',
                 'neon': 'Neon Core', 'signature': 'Signature Panel', 'pulse': 'Power Stripe'}
DEFAULT_BANNER_STYLE = 'pulse'
DEFAULT_I_STYLE = 'bold'
I_STYLES = {'current': 'Current', 'thin': 'Thin Stem', 'bold': 'Bold Stem', 'bar': 'Continuous Stem'}
DENSE_I_GLYPHS = {'thin': (' # ', '   ', ' # ', ' # ', ' # '),
                  'bold': ('## ', '   ', '## ', '## ', '## '),
                  'bar': ('## ', '## ', '## ', '## ', '## ')}
DENSE_WORDMARK = {
    'w': ('##       ##', '##   #   ##', '##  ###  ##', '## ## ## ##', ' ###   ### '),
    'i': (' # ', '###', ' # ', ' # ', '###'),
    's': (' ##### ', '##     ', ' ##### ', '     ##', '###### '),
    't': (' ##  ', '#####', ' ##  ', ' ##  ', '  ###'),
    'r': ('## ###', '###  #', '##    ', '##    ', '##    '),
    'o': (' ##### ', '##   ##', '##   ##', '##   ##', ' ##### '),
    'n': ('## ### ', '###  ##', '##   ##', '##   ##', '##   ##'),
}
WORDMARK = {
    "w": (" _       _ ", "| |     | |", "| | / \\ | |", "| |/ _ \\| |", " \\__/ \\__/ "),
    "i": (" _ ", "(_)", "| |", "| |", "|_|"),
    "s": (" ____  ", "/ ___| ", "\\___ \\ ", " ___) |", "|____/ "),
    "t": (" _   ", "| |_ ", "| __|", "| |_ ", " \\__|"),
    "r": (" _ __ ", "| '__|", "| |   ", "| |   ", "|_|   "),
    "o": ("  ___  ", " / _ \\ ", "| | | |", "| |_| |", " \\___/ "),
    "n": (" _ __  ", "| '_ \\ ", "| | | |", "| | | |", "|_| |_|"),
}


def wordmark_lines():
    return [[(WORDMARK[letter][row] + (" " if index < 6 else ""),
              "green" if letter == "i" and row >= 2 else "blue")
             for index, letter in enumerate("wistron")] for row in range(5)]


def dense_wordmark_lines(block='█', i_style='current'):
    glyph = DENSE_WORDMARK['i'] if i_style == 'current' else DENSE_I_GLYPHS[i_style]
    green_start = 1 if i_style == 'bar' else 2
    return [[((glyph if letter == 'i' else DENSE_WORDMARK[letter])[row].replace('#', block) + (' ' if index < 6 else ''),
              'green' if letter == 'i' and row >= green_start else 'blue')
             for index, letter in enumerate('wistron')] for row in range(5)]


def plain(value):
    return "".join(char if unicodedata.category(char) != "Cc" else " "
                   for char in ANSI.sub("", str(value)))


def cells(text):
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
               for char in text)


def fit(value, width):
    result = []
    used = 0
    width = max(0, width)
    for char in plain(value):
        size = cells(char)
        if used + size > width:
            break
        result.append(char)
        used += size
    return ''.join(result)


def metadata_defaults(value):
    return {'branch': value, 'commit': (value, ''), 'status': value, 'version': value}


class MetadataCache:
    """One reader publishes snapshots; only the main thread touches curses."""

    def __init__(self, settings, interval=5):
        self.settings = settings
        self.interval = interval
        self.stop = threading.Event()
        self.condition = threading.Condition()
        self.values = metadata_defaults('Loading...')
        self.revision = 0
        self.requests = []
        self.thread = threading.Thread(target=self._run, name='build-menu-metadata', daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.condition:
            return dict(self.values), self.revision

    def refresh_now(self):
        # Enqueue a new read after any in-flight read; never reuse a stale pass.
        request = {'done': threading.Event()}
        with self.condition:
            if self.stop.is_set():
                raise RuntimeError('Source metadata reader stopped.')
            self.requests.append(request)
            self.condition.notify()
        while not request['done'].wait(0.05):
            if self.stop.is_set():
                raise RuntimeError('Source metadata reader stopped.')
        if 'values' not in request:
            raise RuntimeError('Source metadata reader stopped.')
        return request['values']

    def _run(self):
        next_read = 0
        try:
            while not self.stop.is_set():
                with self.condition:
                    while not self.stop.is_set() and not self.requests and time.monotonic() < next_read:
                        self.condition.wait(next_read - time.monotonic())
                    if self.stop.is_set():
                        break
                    request = self.requests.pop(0) if self.requests else None
                try:
                    values = self.settings.read_source_metadata(self.stop)
                except Exception:
                    if self.stop.is_set():
                        break
                    values = dict.fromkeys(self.values)
                fresh = metadata_defaults('unavailable')
                with self.condition:
                    previous = dict(self.values)
                    for name, value in values.items():
                        if value is not None:
                            self.values[name] = value
                            fresh[name] = value
                        elif self.values[name] == metadata_defaults('Loading...')[name]:
                            self.values[name] = fresh[name]
                    if self.values != previous:
                        self.revision += 1
                    if request:
                        request.update(values=fresh)
                        request['done'].set()
                next_read = time.monotonic() + self.interval
        finally:
            self.stop.set()
            with self.condition:
                for request in self.requests:
                    request['done'].set()

    def close(self):
        self.stop.set()
        with self.condition:
            self.condition.notify_all()
        self.thread.join()


def wrap(value, width):
    lines = []
    for paragraph in str(value).splitlines() or [""]:
        paragraph = plain(paragraph)
        if not paragraph:
            lines.append("")
        while paragraph:
            chunk = fit(paragraph, max(2, width))
            lines.append(chunk)
            paragraph = paragraph[len(chunk):]
    return lines


def move_selection(index, key, count):
    if key in (curses.KEY_UP, curses.KEY_BTAB):
        return (index - 1) % count
    if key in (curses.KEY_DOWN, 9):
        return (index + 1) % count
    if key == curses.KEY_HOME:
        return 0
    if key == curses.KEY_END:
        return count - 1
    return index


def result_lines(result):
    lines = ["Status: " + result.get("status", "unknown").replace("_", " ").title()]
    if result.get("flavor"):
        lines.append("Customer: " + result["flavor"].title())
    if result.get("error"):
        lines.extend(["", "Error:", str(result["error"])])
    if result.get("artifact"):
        if result.get("image_version"):
            lines.append("Image version: " + result["image_version"])
        lines.extend(["", "Installer: " + str(result["artifact"]),
                      "Size: " + str(result.get("image_bytes", "unknown")) + " bytes",
                      "SHA-256: " + str(result.get("sha256", "unknown"))])
    for stage in result.get("stages", []):
        lines.append(stage.get("phase", "Stage") + ": exit " + str(stage.get("exit_code", "pending")))
    if result.get("log"):
        lines.extend(["", "Full log: " + result["log"],
                      "Component logs: " + str(os.path.join(os.path.dirname(result["log"]), "component-logs"))])
    if "slave_images_unchanged" in result:
        lines.append("Slave image IDs unchanged: " + str(result["slave_images_unchanged"]))
    return lines


class Console:
    def __init__(self, screen, settings, validate, images, selection=0, metadata=None, banner_style=None, i_style=None):
        self.screen = screen
        self.settings = settings
        self.validate = validate
        self.images = images
        self.selection = selection
        self.metadata = metadata
        self.banner_style = DEFAULT_BANNER_STYLE if banner_style is None else banner_style
        if self.banner_style not in BANNER_STYLES:
            raise ValueError('Unknown banner style: ' + str(self.banner_style))
        self.i_style = DEFAULT_I_STYLE if i_style is None else i_style
        if self.i_style not in I_STYLES:
            raise ValueError('Unknown i style: ' + str(self.i_style))
        self.metadata_values = metadata_defaults('Loading...')
        self.draw_token = None
        self.palette = {}
        try:
            "╭╮╰╯─│".encode(sys.stdout.encoding or "utf-8")
            self.border = ("╭", "╮", "╰", "╯", "─", "│")
        except UnicodeError:
            self.border = ("+", "+", "+", "+", "-", "|")
        self.screen.keypad(True)
        self.screen.timeout(50)
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(100)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        if "NO_COLOR" not in os.environ and curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                background = -1
            except curses.error:
                background = curses.COLOR_BLACK
            # Closest xterm-256 colors to the user-provided Wistron wordmark.
            colors = {"blue": 24 if curses.COLORS >= 256 else curses.COLOR_BLUE,
                      "green": 113 if curses.COLORS >= 256 else curses.COLOR_GREEN, "red": curses.COLOR_RED,
                      "yellow": curses.COLOR_YELLOW}
            for pair, (name, foreground) in enumerate(colors.items(), 1):
                curses.init_pair(pair, foreground, background)
                self.palette[name] = curses.color_pair(pair)
            curses.init_pair(5, curses.COLOR_BLACK, colors["green"])
            self.palette["selection"] = curses.color_pair(5)
            if self.banner_style in ('neon', 'signature', 'pulse'):
                blue, green = colors['blue'], colors['green']
                cyan = 39 if curses.COLORS >= 256 else curses.COLOR_CYAN
                white = 255 if curses.COLORS >= 256 else curses.COLOR_WHITE
                navy = 17 if curses.COLORS >= 256 else curses.COLOR_BLUE
                fills = {'glow': (cyan, navy), 'glow_green': (green, navy), 'night': (white, navy),
                         'cyan_band': (curses.COLOR_BLACK, cyan), 'lime_band': (curses.COLOR_BLACK, green),
                         'paper': (blue, white), 'paper_green': (green, white),
                         'ocean': (white, blue), 'ocean_green': (green, blue)}
                for pair, (name, (foreground, backdrop)) in enumerate(fills.items(), 6):
                    curses.init_pair(pair, foreground, backdrop)
                    self.palette[name] = curses.color_pair(pair)

    def close(self):
        if self.metadata is not None:
            self.metadata.close()

    def read_metadata(self):
        if self.metadata is None:
            self.metadata = MetadataCache(self.settings)
        self.metadata_values, revision = self.metadata.snapshot()
        return revision

    @property
    def version_display(self):
        return self.settings.build_version or 'Default: ' + self.metadata_values['version']

    @property
    def source_status(self):
        return self.metadata_values['status']

    def read_key(self, view, render):
        while True:
            revision = self.read_metadata()
            token = (view, self.screen.getmaxyx(), revision, self.settings.arch,
                     self.settings.flavor, self.settings.mode, self.settings.jobs,
                     self.settings.build_version)
            if token != self.draw_token:
                if self.ready():
                    render()
                self.draw_token = token
            key = self.screen.getch()
            if key != -1:
                return key

    def attr(self, tone=None, *, bold=False, dim=False, selected=False):
        return ((self.palette.get("selection", curses.A_REVERSE) if selected else self.palette.get(tone, 0))
                | (curses.A_BOLD if bold else 0) | (curses.A_DIM if dim else 0))

    def put(self, y, x, text, attr=0, width=None):
        height, columns = self.screen.getmaxyx()
        if not (0 <= y < height and 0 <= x < columns):
            return
        available = columns - x - (1 if y == height - 1 else 0)
        if width is not None:
            available = min(available, width)
        if available <= 0:
            return
        try:
            self.screen.addstr(y, x, fit(text, available), attr)
        except curses.error:
            pass  # A resize may occur between getmaxyx and addstr.

    def frame(self, y, x, height, width, title, tone="blue"):
        tl, tr, bl, br, horizontal, vertical = self.border
        self.put(y, x, tl + horizontal * (width - 2) + tr, self.attr(tone))
        self.put(y, x + 2, " " + title + " ", self.attr(tone, bold=True), width - 4)
        for row in range(1, height - 1):
            self.put(y + row, x, vertical, self.attr(tone))
            self.put(y + row, x + width - 1, vertical, self.attr(tone))
        self.put(y + height - 1, x, bl + horizontal * (width - 2) + br, self.attr(tone))

    def supported(self):
        height, columns = self.screen.getmaxyx()
        return height >= 20 and columns >= 48

    def ready(self):
        if self.supported():
            return True
        self.screen.erase()
        self.put(1, 1, "Terminal too small: use at least 48 x 20.", self.attr("yellow", bold=True))
        self.put(3, 1, "Resize to continue. Esc / q / 0 exits.")
        self.screen.refresh()
        return False

    def dimensions(self):
        height, columns = self.screen.getmaxyx()
        width = min(columns - 4, 106)
        return height, width, (columns - width) // 2

    def draw_banner(self, height, width, left, expanded):
        style = self.banner_style
        if style in ('neon', 'signature', 'pulse'):
            self.draw_dense_banner(height, width, left, expanded)
            return
        if style == 'classic':
            self.frame(1, left, height, width, 'SONiC BUILD CONSOLE')
        else:
            unicode_border = self.border[0] != '+'
            if style == 'tech':
                border = ('╔', '╗', '╚', '╝', '═', '║') if unicode_border else ('+', '+', '+', '+', '=', '|')
                title = '[ SONiC BUILD CONSOLE ]'
                footer = '--====--+--====--+--====--'
            elif style == 'brand':
                border = self.border
                title = ' SONiC BUILD CONSOLE '
                footer = ' / WISTRON NETWORKING / '
            else:
                border = ('+', '+', '+', '+', '-', '|')
                title = ' >> SONiC BUILD CONSOLE << '
                footer = '// SONiC :: WISTRON //'
            tl, tr, bl, br, horizontal, vertical = border
            line = (('-.' * width)[:width - 2] if style == 'retro' else horizontal * (width - 2))
            self.put(1, left, tl + line + tr, self.attr('blue'))
            self.put(height, left, bl + line + br, self.attr('blue'))
            for row in range(2, height):
                self.put(row, left, vertical, self.attr('blue'))
                self.put(row, left + width - 1, vertical, self.attr('blue'))
            for row, column, char in ((1, left, tl), (1, left + width - 1, tr),
                                      (height, left, bl), (height, left + width - 1, br)):
                self.put(row, column, char, self.attr('green', bold=style != 'brand'))
            self.put(1, left + (width - cells(title)) // 2, title, self.attr('blue', bold=True))
            footer = fit(footer, width - 6)
            self.put(height, left + (width - cells(footer)) // 2, footer, self.attr('green', bold=True))
            if style == 'brand':
                self.put(1, left + 3, '//', self.attr('green', bold=True))
                self.put(1, left + width - 5, '\\\\', self.attr('green', bold=True))
        if expanded:
            start = left + (width - 52) // 2
            for row, parts in enumerate(wordmark_lines()):
                x = start
                for text, tone in parts:
                    self.put(3 + row, x, text, self.attr(tone, bold=True))
                    x += cells(text)
            if style == 'tech':
                for row in range(3, 8):
                    tick = '+' if row in (3, 7) else '|'
                    self.put(row, left + 2, tick, self.attr('blue', dim=True))
                    self.put(row, left + width - 3, tick, self.attr('blue', dim=True))
                if start - left >= 12:
                    self.put(5, start - 11, '// >>>', self.attr('green', bold=True))
                    self.put(5, start + 57, '<<< \\\\', self.attr('green', bold=True))
            elif style == 'brand' and start - left >= 10:
                wings = ('   / /', '  / / ', ' / /  ', '  \\ \\ ', '   \\ \\')
                for row, wing in enumerate(wings, 3):
                    mirrored = wing[::-1].translate(str.maketrans('/\\', '\\/'))
                    self.put(row, start - 9, wing, self.attr('blue', bold=True))
                    self.put(row, start + 55, mirrored, self.attr('blue', bold=True))
            elif style == 'retro':
                for row, tick in enumerate(('+', ':', '>', ':', '+'), 3):
                    self.put(row, left + 1, tick, self.attr('green'))
                    self.put(row, left + width - 2, '<' if tick == '>' else tick, self.attr('green'))
                if start - left >= 10:
                    for row, marks in enumerate(('. :: .', '. . : ', '>> :: ', ' : . .', '. :: .'), 3):
                        self.put(row, start - 10, marks, self.attr('blue', dim=True))
                        self.put(row, start + 56, marks[::-1].replace('>', '<'), self.attr('blue', dim=True))
        else:
            self.put(2, left + 3, 'w', self.attr('blue', bold=True))
            self.put(2, left + 4, 'i', self.attr('green', bold=True))
            self.put(2, left + 5, 'stron  /  CACHES OFF  /  SLAVE PINNED', self.attr('blue', bold=True), width - 8)

    def draw_dense_banner(self, height, width, left, expanded):
        style = self.banner_style
        body, green = {'neon': ('glow', 'glow_green'), 'signature': ('paper', 'paper_green'),
                       'pulse': ('ocean', 'ocean_green')}[style]
        top_tone = 'lime_band' if style == 'pulse' else 'ocean' if style == 'signature' else 'cyan_band'
        bottom_tone = 'lime_band' if style == 'pulse' else 'ocean' if style == 'signature' else 'lime_band'
        for row in range(1, height + 1):
            self.put(row, left, ' ' * width, self.attr(body))
        self.put(1, left, ' ' * width, self.attr(top_tone))
        self.put(height, left, ' ' * width, self.attr(bottom_tone))
        title = 'SONiC BUILD CONSOLE'
        footer = {'neon': '<<< WISTRON NETWORKING >>>', 'signature': 'WISTRON NETWORKING',
                  'pulse': '/// WISTRON NETWORKING ///'}[style]
        for row, text, tone in ((1, title, top_tone), (height, footer, bottom_tone)):
            self.put(row, left + (width - len(text)) // 2, text, self.attr(tone, bold=True))
            if style != 'signature':
                trim = '// ' if row == 1 else ' ///'
                self.put(row, left + 2, trim, self.attr(tone, bold=True))
                self.put(row, left + width - len(trim) - 2, trim[::-1], self.attr(tone, bold=True))
        if style == 'signature':
            for row in (1, height):
                self.put(row, left, ' ' * 5, self.attr('lime_band'))
                self.put(row, left + width - 5, ' ' * 5, self.attr('lime_band'))
        if expanded:
            block = '█' if self.border[0] != '+' else '#'
            start = left + (width - 52) // 2
            for row, parts in enumerate(dense_wordmark_lines(block, self.i_style), 3):
                x = start
                for text, tone in parts:
                    self.put(row, x, text, self.attr(green if tone == 'green' else body, bold=True))
                    x += cells(text)
            if start - left >= 14:
                if style == 'neon':
                    shapes = ('   /\\   ', ' /====\\ ', '<| || |>', ' \\====/ ', '   \\/   ')
                elif style == 'signature':
                    shapes = ('    ///', '  /////', '///////', '  /////', '    ///')
                else:
                    shapes = ('////   ', ' ////  ', '  //// ', ' ////  ', '////   ')
                for row, shape in enumerate(shapes, 3):
                    tone = green if row in (3, 5, 7) else body
                    self.put(row, start - 15, shape, self.attr(tone, bold=True))
                    mirror = shape[::-1].translate(str.maketrans('/\\<>', '\\/><'))
                    self.put(row, start + 59, mirror, self.attr(tone, bold=True))
                if style == 'pulse':
                    for row in range(3, 8):
                        self.put(row, left + 2, '  ', self.attr('lime_band'))
                        self.put(row, left + width - 4, '  ', self.attr('lime_band'))
        else:
            label = 'wistron'
            start = left + (width - len(label)) // 2
            self.put(2, start, label[0], self.attr(body, bold=True))
            self.put(2, start + 1, label[1], self.attr(green, bold=True))
            self.put(2, start + 2, label[2:], self.attr(body, bold=True))

    def draw_main(self, *, refresh=True):
        self.read_metadata()
        self.screen.erase()
        height, width, left = self.dimensions()
        expanded = height >= 24 and width >= 58
        header_height = 9 if expanded else 3
        self.draw_banner(header_height, width, left, expanded)
        top = header_height + 2
        self.frame(top, left, 10, width, "BUILD CONFIGURATION")
        values = ("ARM64: Marvell Prestera" if self.settings.arch == "arm64" else "X86: AMD64 - not supported",
                  "Amazon" if self.settings.flavor == "amazon" else "Wistron",
                  "Yes: back up target and rebuild" if self.settings.mode == "clean" else "No: retain outputs, no caches",
                  str(self.settings.jobs) + " concurrent jobs", self.version_display,
                  "configure -> build -> verify", "saved logs, checksum, status", "")
        column = min(26, max(23, width // 3))
        for index, (label, value) in enumerate(zip(OPTIONS, values)):
            row = top + 1 + index
            number = index + 1 if index < 7 else 0
            selected = index == self.selection
            tone = "blue"
            self.put(row, left + 1, " " * (width - 2), self.attr(tone, selected=selected))
            self.put(row, left + 2, ("> " if selected else "  ") + str(number) + ". " + label,
                     self.attr(tone, bold=selected, selected=selected), column - 2)
            self.put(row, left + column, value,
                     self.attr(bold=True, selected=selected), width - column - 2)
        path_top = top + 11
        branch, status = self.metadata_values['branch'], self.source_status
        commit, subject = self.metadata_values['commit']
        commit_display = " ".join(part for part in (commit, subject) if part)
        fields = (("Git branch", branch, None), ("Git commit", commit_display, None),
                  ("Source status", status, "green" if status == "Clean" else "yellow"))
        if height - path_top >= 9:
            self.frame(path_top, left, 7, width, "SOURCE SNAPSHOT")
            self.put(path_top + 1, left + 2, "Source: " + str(self.settings.source), width=width - 4)
            self.put(path_top + 2, left + 2, "Build:  " + str(self.settings.workspace), width=width - 4)
            field_top = path_top + 3
        else:
            field_top = path_top - 1
            available = height - 2 - field_top
            if available < 3:
                if available == 1:
                    suffix = " @ " + commit + " [" + status + "]"
                    value = "Git: " + fit(branch, width - 4 - cells("Git: " + suffix)) + suffix
                    self.put(field_top, left + 2, value, self.attr(bold=True), width - 4)
                else:
                    self.put(field_top, left + 2, "Git branch: " + branch, self.attr(bold=True), width - 4)
                    self.put(field_top + 1, left + 2, "Git commit: " + commit + " | Source status: " + status,
                             self.attr(bold=True), width - 4)
                fields = ()
            else:
                if height - 2 - (field_top + 3) >= 2:
                    for row, text in enumerate(("Source: " + str(self.settings.source),
                                                "Build:  " + str(self.settings.workspace)), field_top + 3):
                        self.put(row, left + 2, text, self.attr(dim=True), width - 4)
        for row, (label, value, tone) in enumerate(fields, field_top):
            self.put(row, left + 2, label + ":", self.attr("blue"))
            self.put(row, left + 17, value, self.attr(tone, bold=True), width - 19)
        hint = ("Up/Down navigate  Enter select  Esc back  1-7/0 shortcuts" if width >= 70
                else "Up/Down | Enter select | Esc back")
        self.put(height - 2, left + 1, hint, self.attr(dim=True), width - 2)
        if refresh:
            self.screen.refresh()

    def choose(self, title, options, selected, shortcuts=None):
        self.draw_token = None
        def choices():
            return options() if callable(options) else options
        def render():
            self.draw_main(refresh=False)
            height, width, left = self.dimensions()
            width = min(width - 4, 88)
            x = left + (self.dimensions()[1] - width) // 2
            current_options = choices()
            top = max(1, (height - len(current_options) - 5) // 2)
            popup_height = len(current_options) + 5
            for row in range(popup_height):
                self.put(top + row, x, " " * width)
            self.frame(top, x, popup_height, width, title)
            for index, option in enumerate(current_options):
                active = index == selected
                self.put(top + 2 + index, x + 2, fit(("> " if active else "  ") + str(index + 1) + ". " + option, width - 4).ljust(width - 4),
                         self.attr("green" if active else None, bold=active, selected=active), width - 4)
            self.put(top + popup_height - 2, x + 2, "Up/Down choose  Enter apply  Esc cancel", self.attr(dim=True), width - 4)
            self.screen.refresh()
        while True:
            key = self.read_key(('choose', title, selected), render)
            if not self.supported():
                if key in BACK or key in (ord('q'), ord('0'), 4):
                    return None
                continue
            if key in BACK or key == 4:
                return None
            if key in ENTER:
                return selected
            if ord('1') <= key < ord('1') + len(choices()):
                selected = key - ord('1')
            elif 0 <= key < 256 and shortcuts and chr(key).lower() in shortcuts:
                selected = shortcuts[chr(key).lower()]
            else:
                selected = move_selection(selected, key, len(choices()))

    def message(self, title, lines, tone="blue", confirm=False):
        offset = 0
        visible, content = 0, []
        self.draw_token = None
        def render():
            nonlocal offset, visible, content
            self.draw_main(refresh=False)
            height, width, left = self.dimensions()
            width = min(width - 2, 100)
            x = left + (self.dimensions()[1] - width) // 2
            content = [part for line in lines for part in wrap(line, width - 4)]
            popup_height = min(height - 4, max(8, len(content) + 4))
            top = (height - popup_height) // 2
            for row in range(popup_height):
                self.put(top + row, x, " " * width)
            self.frame(top, x, popup_height, width, title, tone)
            visible = popup_height - 4
            offset = min(offset, max(0, len(content) - visible))
            for row, line in enumerate(content[offset:offset + visible]):
                self.put(top + 2 + row, x + 2, line, width=width - 4)
            hint = "Enter / y start  Esc / n back" if confirm else "Enter / Esc back"
            if len(content) > visible:
                hint = "Up/Down scroll  " + hint
            self.put(top + popup_height - 2, x + 2, hint, self.attr(dim=True), width - 4)
            self.screen.refresh()
        while True:
            key = self.read_key(('message', title, offset), render)
            if not self.supported():
                if key in BACK or key in (ord('q'), ord('0'), 4):
                    return False
                continue
            if key in BACK or key in (4, ord('n') if confirm else ord('q')):
                return False
            if key in ENTER or (confirm and key in (ord('y'), ord('Y'))):
                return True
            if key in (curses.KEY_UP, curses.KEY_PPAGE):
                offset = max(0, offset - (visible if key == curses.KEY_PPAGE else 1))
            elif key in (curses.KEY_DOWN, curses.KEY_NPAGE):
                offset = min(max(0, len(content) - visible), offset + (visible if key == curses.KEY_NPAGE else 1))

    def edit_jobs(self):
        def apply(value):
            try:
                jobs = int(value.strip() or "8")
                if jobs <= 0:
                    raise ValueError
            except ValueError:
                raise ValueError("Build job must be a positive integer.") from None
            self.settings.jobs = jobs
        self.edit_text("BUILD JOB", "SONIC_BUILD_JOBS", str(self.settings.jobs), "8", apply)

    def edit_version(self):
        chosen = self.choose('BUILD VERSION',
                             lambda: ['Default: ' + self.metadata_values['version'],
                                      'Custom: enter a version'],
                             1 if self.settings.build_version else 0, {'d': 0, 'c': 1})
        if chosen == 0:
            self.settings.set_build_version('')
        elif chosen == 1:
            self.edit_text('CUSTOM BUILD VERSION', 'SONIC_IMAGE_VERSION', lambda: self.version_display,
                           'Default', self.settings.set_build_version)

    def edit_text(self, title, prompt, current, default, apply):
        value = ""
        cursor = 0
        error = ""
        self.draw_token = None
        def render():
            self.draw_main(refresh=False)
            height, width, left = self.dimensions()
            width = min(width - 4, 88)
            x = left + (self.dimensions()[1] - width) // 2
            top = (height - 9) // 2
            for row in range(9):
                self.put(top + row, x, " " * width)
            self.frame(top, x, 9, width, title)
            self.put(top + 2, x + 2, "Current: " + (current() if callable(current) else current), width=width - 4)
            self.put(top + 3, x + 2, prompt + " >", self.attr("blue"), width - 4)
            room = width - 4
            start = max(0, cursor - room + 1)
            self.put(top + 4, x + 2, value[start:start + room].ljust(room), self.attr(bold=True), room)
            self.put(top + 5, x + 2, error, self.attr("red"), room)
            self.put(top + 7, x + 2, "Enter apply (blank=" + default + ")  Esc cancel  Ctrl+U clear", self.attr(dim=True), room)
            self.screen.move(top + 4, x + 2 + cursor - start)
            self.screen.refresh()
        try:
            curses.curs_set(1)
            while True:
                key = self.read_key(('edit', title, value, cursor, error), render)
                if not self.supported():
                    if key in BACK or key in (ord('q'), ord('0'), 4):
                        return
                    continue
                if key in BACK or key == 4:
                    return
                if key in ENTER:
                    try:
                        apply(value.strip())
                    except (ValueError, RuntimeError) as exception:
                        error = str(exception)
                    else:
                        return
                elif key in (curses.KEY_BACKSPACE, 127, 8) and cursor:
                    value = value[:cursor - 1] + value[cursor:]
                    cursor -= 1
                elif key == curses.KEY_DC:
                    value = value[:cursor] + value[cursor + 1:]
                elif key == curses.KEY_LEFT:
                    cursor = max(0, cursor - 1)
                elif key == curses.KEY_RIGHT:
                    cursor = min(len(value), cursor + 1)
                elif key == curses.KEY_HOME:
                    cursor = 0
                elif key == curses.KEY_END:
                    cursor = len(value)
                elif key == 21:
                    value, cursor = "", 0
                elif 32 <= key <= 126:
                    value = value[:cursor] + chr(key) + value[cursor:]
                    cursor += 1
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

    def show_result(self, result):
        status = result.get("status", "unknown")
        tone = {"success": "green", "failed": "red", "interrupted": "yellow"}.get(status, "blue")
        self.message("BUILD RESULT / " + status.upper(), result_lines(result), tone=tone)

    def loop(self, result=None):
        if result:
            self.show_result(result)
        while True:
            key = self.read_key(('main', self.selection), self.draw_main)
            if not self.supported():
                if key in BACK or key in (ord('q'), ord('0'), 4):
                    return "exit", self.selection
                continue
            if key in (ord('q'), 4) or key in BACK:
                return "exit", self.selection
            if ord('1') <= key <= ord('7') or key == ord('0'):
                self.selection = 7 if key == ord('0') else key - ord('1')
            elif key not in ENTER:
                self.selection = move_selection(self.selection, key, len(OPTIONS))
            elif self.selection == 7:
                return "exit", self.selection
            elif self.selection == 0:
                chosen = self.choose("ARCHITECTURE", ["ARM64: Marvell Prestera", "X86: AMD64 (not supported yet)"],
                                     0 if self.settings.arch == "arm64" else 1)
                if chosen is not None:
                    self.settings.arch = "arm64" if chosen == 0 else "amd64"
            elif self.selection == 1:
                chosen = self.choose("CUSTOMER", ["Amazon (ZTP)", "Wistron (no ZTP)"],
                                     0 if self.settings.flavor == "amazon" else 1)
                if chosen is not None:
                    self.settings.flavor = "amazon" if chosen == 0 else "wistron"
            elif self.selection == 2:
                chosen = self.choose("CLEAN REBUILD", ["Yes: back up target and rebuild", "No: retain outputs, caches disabled"],
                                     0 if self.settings.mode == "clean" else 1, {"y": 0, "n": 1})
                if chosen is not None:
                    self.settings.mode = "clean" if chosen == 0 else "normal"
            elif self.selection == 3:
                self.edit_jobs()
            elif self.selection == 4:
                self.edit_version()
            elif self.selection == 5:
                if self.settings.arch != "arm64":
                    self.message("NOT SUPPORTED", ["X86 / AMD64 builds are not supported yet.", "No build was started."], "yellow")
                    continue
                try:
                    self.validate()
                except (RuntimeError, OSError, ValueError) as error:
                    self.message("CANNOT START BUILD", [str(error)], "red")
                    continue
                fresh = self.metadata.refresh_now()
                commit_display = ' '.join(part for part in fresh['commit'] if part)
                details = ["Architecture: ARM64: Marvell Prestera",
                           "Customer: " + self.settings.flavor.title(),
                           "Clean rebuild: " + ("Yes" if self.settings.mode == "clean" else "No"),
                           "Build job: " + str(self.settings.jobs), "Package/version caches: OFF",
                           "Build version: " + (self.settings.build_version or 'Default: ' + fresh['version']),
                           "ZTP: " + ("ON" if self.settings.ztp == "y" else "OFF"), "",
                           "Git branch: " + fresh['branch'],
                           "Git commit: " + commit_display,
                           "Source status: " + fresh['status'],
                           "Source: " + str(self.settings.source), "Build directory: " + str(self.settings.workspace), ""]
                details.extend(distro.title() + ": " + image for distro, image in self.images.items())
                details.extend(["", "The console will switch to the full foreground build log.",
                                "Ctrl+C stops this run. The result card appears after completion."])
                if self.message("START BUILD / REVIEW", details, "green", confirm=True):
                    return "build", self.selection
            elif self.selection == 6:
                try:
                    path = self.settings.state / "last-run.json"
                    if path.exists():
                        self.show_result(json.loads(path.read_text()))
                    else:
                        self.message("LAST RESULT", ["No previous build result."], "yellow")
                except (OSError, ValueError) as error:
                    self.message("CANNOT READ RESULT", [str(error)], "red")


def run_menu(settings, build, validate, images):
    """Restore terminal mode before running the existing foreground builder."""
    selection = 0
    result = None
    def session(screen):
        console = Console(screen, settings, validate, images, selection)
        try:
            return console.loop(result)
        finally:
            console.close()
    while True:
        try:
            action, selection = curses.wrapper(session)
        except KeyboardInterrupt:
            result = None
            continue
        result = None
        if action == "exit":
            return 0
        try:
            result = build()
        except KeyboardInterrupt:
            result = {"status": "interrupted", "error": "Stopped by the user with Ctrl+C."}
        except (RuntimeError, OSError, ValueError) as error:
            result = {"status": "failed", "error": str(error)}
