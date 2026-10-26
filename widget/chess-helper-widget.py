#!/usr/bin/python3
"""Independent GTK Chess Helper control card; other desktop widgets are untouched."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import signal
import subprocess
import threading

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk, Pango

MODES = [('fast', 'Cepat · 1 detik'), ('balanced', 'Seimbang · 3 detik'),
         ('strong', 'Kuat · 10 detik'), ('maximum', 'Maksimal · 30 detik')]
CSS = b'''
window#chess-helper-widget { background: #171514; color: #f2e9e0; border: 1px solid #62442f; border-radius: 15px; }
#chess-helper-widget label { color: #e7ded5; font: 9pt Lato, Cantarell, sans-serif; }
#chess-helper-widget label.title { color: #fff1e1; font-size: 14pt; font-weight: 700; }
#chess-helper-widget label.eyebrow { color: #d7a16f; font-size: 8pt; font-weight: 700; }
#chess-helper-widget label.muted { color: #a99789; font-size: 8pt; }
#chess-helper-widget label.icon { color: #e3b66e; font-size: 27pt; }
#chess-helper-widget label.badge { color: #c6b4a4; background: #30251c; border: 1px solid #57402c; border-radius: 8px; padding: 4px 7px; font-size: 8pt; }
#chess-helper-widget label.good { color: #a6d88e; }
#chess-helper-widget label.warn { color: #ffc181; }
#chess-helper-widget .section { background: #201c19; border: 1px solid #392e26; border-radius: 10px; padding: 9px; }
#chess-helper-widget button { color: #efc7a5; background: #28211c; background-image: none; border: 1px solid #493729; border-radius: 8px; box-shadow: none; text-shadow: none; min-height: 27px; padding: 3px 10px; }
#chess-helper-widget button label { font-size: 9pt; }
#chess-helper-widget button:hover { background: #3a2a1e; border-color: #9b6537; }
#chess-helper-widget button:disabled { background: #211d1a; border-color: #362b23; }
#chess-helper-widget button:disabled label { color: #76685d; }
#chess-helper-widget button.primary { background: #d89556; border-color: #e6a86e; }
#chess-helper-widget button.primary label { color: #271a10; font-weight: 700; }
#chess-helper-widget button.primary:hover { background: #f0b273; }
#chess-helper-widget button.primary:disabled { background: #3b3027; border-color: #4c3a2c; }
#chess-helper-widget button.primary:disabled label { color: #a18b79; }
#chess-helper-widget combobox { padding: 0; border: 0; background: transparent; }
#chess-helper-widget combobox button { background: #171514; border-color: #514031; }
#chess-helper-widget switch { min-height: 17px; min-width: 33px; background: #332921; border: 1px solid #61472f; }
#chess-helper-widget switch:checked { background: #9b683d; }
#chess-helper-widget switch slider { background: #e9c8a6; min-height: 15px; min-width: 15px; }
#chess-helper-widget menu { background: #241d18; color: #f1ddca; border: 1px solid #785039; padding: 5px; }
#chess-helper-widget menuitem { min-height: 26px; padding: 4px 10px; }
#chess-helper-widget menuitem:hover { background: #513720; }
'''


def clamp_position(x, y, width, height, area):
    ax, ay, aw, ah = area
    return (max(ax, min(int(x), ax + max(0, aw - width))),
            max(ay, min(int(y), ay + max(0, ah - height))))


class ChessWidget:
    def __init__(self, controller=None, poll=True, show=True):
        if controller is None:
            from chess_widget_core import WidgetController
            controller = WidgetController()
        self.controller = controller
        self.state = controller.load_state()
        self.snapshot_data = dict(running=False, owned=False, mode=self.state.get('mode', 'fast'), error=None)
        self.action_busy = False
        self.refresh_busy = False
        self.syncing = True
        self.dragging = False
        self.position_timer = 0
        self.quitting = False
        self.window = Gtk.Window(title='Chess Helper')
        self.window.set_name('chess-helper-widget')
        self.window.set_wmclass('chess-helper-widget', 'ChessHelperWidget')
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.window.set_focus_on_map(False)
        self.window.stick()
        self.window.connect('delete-event', lambda *_: True)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9, margin=13)
        self.window.add(root)

        # Header is the only drag target; controls never move the card.
        self.header = Gtk.EventBox()
        self.header.set_visible_window(False)
        self.header.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.header.connect('button-press-event', self.header_press)
        self.header.set_tooltip_text('Geser header untuk pindah · klik kanan untuk menu')
        row = Gtk.Box(spacing=9)
        self.header.add(row)
        row.pack_start(self.label('\u265e', 'icon'), False, False, 0)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        names.pack_start(self.label('BOT TRAINER', 'eyebrow'), False, False, 0)
        names.pack_start(self.label('Chess Helper', 'title'), False, False, 0)
        row.pack_start(names, True, True, 0)
        self.status_label = self.label('SIAP', 'badge')
        self.status_label.set_valign(Gtk.Align.CENTER)
        row.pack_end(self.status_label, False, False, 0)
        root.pack_start(self.header, False, False, 0)

        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        section.get_style_context().add_class('section')
        self.detail_label = self.label('Stockfish · kekuatan penuh')
        section.pack_start(self.detail_label, False, False, 0)
        self.move_label = self.label('Tekan Start untuk aktifkan panduan.', 'muted')
        self.move_label.set_max_width_chars(42)
        self.move_label.set_ellipsize(Pango.EllipsizeMode.END)
        section.pack_start(self.move_label, False, False, 0)
        root.pack_start(section, False, False, 0)

        mode_row = Gtk.Box(spacing=9)
        mode_row.pack_start(self.label('Mode', 'muted'), False, False, 0)
        self.mode_combo = Gtk.ComboBoxText()
        for key, label in MODES:
            self.mode_combo.append(key, label)
        self.mode_combo.set_active_id(self.state.get('mode', 'fast'))
        self.mode_combo.connect('changed', self.mode_changed)
        mode_row.pack_end(self.mode_combo, True, True, 0)
        root.pack_start(mode_row, False, False, 0)

        buttons = Gtk.Box(spacing=8, homogeneous=True)
        self.start_button = Gtk.Button(label='\u25b6  Start')
        self.start_button.get_style_context().add_class('primary')
        self.start_button.set_tooltip_text('Nyalakan server dan panduan di Chrome')
        self.start_button.connect('clicked', lambda *_: self.run_action(self.controller.start, 'Menyalakan helper…'))
        self.stop_button = Gtk.Button(label='\u25a0  Stop')
        self.stop_button.set_tooltip_text('Hentikan server dan hilangkan panah')
        self.stop_button.connect('clicked', lambda *_: self.run_action(self.controller.stop, 'Menghentikan helper…'))
        buttons.pack_start(self.start_button, True, True, 0)
        buttons.pack_start(self.stop_button, True, True, 0)
        root.pack_start(buttons, False, False, 0)

        footer = Gtk.Box(spacing=7)
        footer.pack_start(self.label('Tetap di atas', 'muted'), False, False, 0)
        self.pin_switch = Gtk.Switch()
        self.pin_switch.set_active(self.state.get('pin', True))
        self.pin_switch.set_valign(Gtk.Align.CENTER)
        self.pin_switch.connect('notify::active', self.pin_changed)
        footer.pack_start(self.pin_switch, False, False, 0)
        open_button = Gtk.Button(label='Buka bot \u2197')
        open_button.connect('clicked', lambda *_: subprocess.Popen(['xdg-open', 'https://www.chess.com/play/computer'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        footer.pack_end(open_button, False, False, 0)
        root.pack_start(footer, False, False, 0)
        self.window.set_default_size(320, 280)
        self.syncing = False
        self.apply_pin()
        self.render(self.snapshot_data)
        if show:
            self.window.show_all()
            GLib.idle_add(self.place_window)
        self.window.connect('configure-event', self.configured)
        display = Gdk.Display.get_default()
        display.connect('monitor-added', lambda *_: GLib.idle_add(self.place_window))
        display.connect('monitor-removed', lambda *_: GLib.idle_add(self.place_window))
        if poll:
            self.refresh()
            GLib.timeout_add(1500, self.refresh)

    @staticmethod
    def label(text='', cls=''):
        item = Gtk.Label(label=text, xalign=0)
        if cls: item.get_style_context().add_class(cls)
        return item

    def apply_pin(self):
        pinned = self.pin_switch.get_active()
        self.window.set_keep_below(not pinned)
        self.window.set_keep_above(pinned)

    def pin_changed(self, *_):
        self.apply_pin()
        if not self.syncing:
            self.controller.save_state({'pin': self.pin_switch.get_active()})

    def run_action(self, function, message):
        if self.action_busy: return
        self.action_busy = True
        self.move_label.set_text(message)
        self.update_buttons()
        def worker():
            try: result = function()
            except Exception as error:
                result = dict(self.snapshot_data, error=str(error))
            GLib.idle_add(self.finish_action, result)
        threading.Thread(target=worker, name='chess-widget-action', daemon=True).start()

    def finish_action(self, result):
        self.action_busy = False
        self.render(result)
        if self.quitting: Gtk.main_quit()
        return False

    def mode_changed(self, combo):
        if self.syncing: return
        mode = combo.get_active_id()
        if mode: self.run_action(lambda: self.controller.set_mode(mode), 'Menyimpan mode…')

    def refresh(self):
        if self.refresh_busy or self.action_busy: return True
        self.refresh_busy = True
        def worker():
            try: result = self.controller.snapshot()
            except Exception as error: result = dict(self.snapshot_data, error=str(error))
            GLib.idle_add(self.finish_refresh, result)
        threading.Thread(target=worker, name='chess-widget-status', daemon=True).start()
        return True

    def finish_refresh(self, result):
        self.refresh_busy = False
        if not self.action_busy: self.render(result)
        return False

    def update_buttons(self):
        running = self.snapshot_data.get('running', False)
        self.start_button.set_sensitive(not running and not self.action_busy)
        self.stop_button.set_sensitive(running and self.snapshot_data.get('owned', False) and not self.action_busy)
        self.mode_combo.set_sensitive(not self.action_busy)

    def render(self, snapshot):
        self.snapshot_data = snapshot
        running = snapshot.get('running', False)
        error = snapshot.get('error')
        busy = snapshot.get('busy', False)
        self.status_label.set_text('PERIKSA' if error else ('AKTIF' if running else 'SIAP'))
        for name in ['good', 'warn']: self.status_label.get_style_context().remove_class(name)
        self.status_label.get_style_context().add_class('warn' if error else 'good' if running else 'muted')
        self.detail_label.set_text('Stockfish · kekuatan penuh' if running else 'Helper berhenti · CPU hemat')
        move = snapshot.get('last_move')
        if isinstance(move, dict): move = move.get('move')
        if error: detail = str(error)
        elif busy: detail = 'Menganalisis posisi papan…'
        elif running and move and isinstance(move, str) and len(move) in [4,5]: detail = f'Panduan terakhir: {move[:2]} → {move[2:4]}'
        elif running: detail = 'Aktif · buka game bot di Chrome.'
        else: detail = 'Tekan Start untuk aktifkan panduan.'
        self.move_label.set_text(detail)
        self.move_label.set_tooltip_text(detail)
        mode = snapshot.get('mode', 'fast')
        self.syncing = True
        self.mode_combo.set_active_id(mode)
        self.syncing = False
        self.update_buttons()

    def place_window(self):
        display = Gdk.Display.get_default()
        state = self.controller.load_state()
        monitor = None
        for i in range(display.get_n_monitors()):
            candidate = display.get_monitor(i)
            if candidate.get_model() == state.get('monitor'):
                monitor = candidate; break
        monitor = monitor or display.get_primary_monitor() or display.get_monitor(0)
        a = monitor.get_workarea(); area = (a.x,a.y,a.width,a.height)
        w,h = self.window.get_size()
        x,y = clamp_position(state.get('x', a.x+24),state.get('y', a.y+a.height-h-20),w,h,area)
        self.window.move(x,y)
        return False

    def header_press(self, _, event):
        if event.button == 1:
            self.dragging = True
            self.window.begin_move_drag(1, int(event.x_root), int(event.y_root), event.time)
        elif event.button == 3:
            menu = Gtk.Menu()
            item = Gtk.MenuItem(label='Tutup widget & hentikan helper')
            item.connect('activate', self.quit)
            menu.append(item); menu.show_all(); menu.popup_at_pointer(event)
        return True

    def configured(self, *_):
        if self.dragging:
            if self.position_timer: GLib.source_remove(self.position_timer)
            self.position_timer = GLib.timeout_add(500,self.save_position)
        return False

    def save_position(self):
        self.position_timer = 0
        pointer = Gdk.Display.get_default().get_default_seat().get_pointer()
        _,_,_,mask = self.window.get_screen().get_root_window().get_device_position(pointer)
        if mask & Gdk.ModifierType.BUTTON1_MASK:
            self.position_timer = GLib.timeout_add(200,self.save_position)
            return False
        self.dragging = False
        x,y = self.window.get_position()
        monitor = Gdk.Display.get_default().get_monitor_at_window(self.window.get_window())
        self.controller.save_state({'x':x,'y':y,'monitor':monitor.get_model()})
        return False

    def quit(self, *_):
        if self.action_busy: return
        self.quitting = True
        if self.snapshot_data.get('owned'): self.run_action(self.controller.stop, 'Menutup helper…')
        else: Gtk.main_quit()


def main():
    lock_path=Path.home()/'.cache/chess-helper/widget.lock'
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    lock_stream=lock_path.open('w')
    try: fcntl.flock(lock_stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print('Chess Helper widget sudah berjalan.'); return
    app = ChessWidget()
    signal.signal(signal.SIGTERM, app.quit)
    signal.signal(signal.SIGINT, app.quit)
    Gtk.main()
    lock_stream.close()

if __name__ == '__main__': main()
