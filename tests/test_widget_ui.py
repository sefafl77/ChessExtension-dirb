"""GTK integration with a fake process controller; real process tests live in test_widget_core.py.
UI regression tests by sefafl.
"""
import importlib.util
import sys
import time
import unittest
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

PATH=Path(__file__).resolve().parents[1] / 'widget' / 'chess-helper-widget.py'

class FakeController:
    def __init__(self): self.state={'mode':'fast','pin':True}; self.running=False; self.calls=[]
    def load_state(self): return dict(self.state)
    def save_state(self, patch): self.state.update(patch); return self.load_state()
    def snapshot(self): return dict(running=self.running,owned=self.running,pid=123 if self.running else None,mode=self.state['mode'],status='Aktif' if self.running else 'Siap',error=None)
    def start(self): self.calls.append('start'); self.running=True; return self.snapshot()
    def stop(self): self.calls.append('stop'); self.running=False; return self.snapshot()
    def set_mode(self,mode): self.calls.append(('mode',mode)); self.state['mode']=mode; return self.snapshot()

class WidgetUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(PATH.parent))
        spec=importlib.util.spec_from_file_location('chess_widget_ui',PATH)
        cls.module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
    def setUp(self):
        self.controller=FakeController()
        self.widget=self.module.ChessWidget(controller=self.controller,poll=False,show=False)
    def tearDown(self): self.widget.window.destroy()
    def settle(self):
        until=time.monotonic()+3
        while time.monotonic()<until:
            while Gtk.events_pending(): Gtk.main_iteration_do(False)
            if not self.widget.action_busy: return
            time.sleep(.01)
        self.fail('GTK action failed to finish')
    def test_start_stop_buttons_control_lifecycle_and_reflect_state(self):
        self.assertTrue(self.widget.start_button.get_sensitive())
        self.assertFalse(self.widget.stop_button.get_sensitive())
        self.widget.start_button.emit('clicked'); self.settle()
        self.assertEqual(self.controller.calls,['start'])
        self.assertFalse(self.widget.start_button.get_sensitive())
        self.assertTrue(self.widget.stop_button.get_sensitive())
        self.widget.stop_button.emit('clicked'); self.settle()
        self.assertEqual(self.controller.calls,['start','stop'])
        self.assertTrue(self.widget.start_button.get_sensitive())
    def test_selecting_mode_persists_without_starting_engine(self):
        self.widget.mode_combo.set_active_id('maximum'); self.settle()
        self.assertEqual(self.controller.state['mode'],'maximum')
        self.assertFalse(self.controller.running)
        self.assertEqual(self.controller.calls,[('mode','maximum')])
    def test_pin_setting_persists(self):
        self.widget.pin_switch.set_active(False)
        self.assertFalse(self.controller.state['pin'])
    def test_geometry_clamps_inside_monitor(self):
        self.assertEqual(self.module.clamp_position(-500,4000,300,290,(0,40,1920,1040)),(0,790))

if __name__=='__main__': unittest.main()
