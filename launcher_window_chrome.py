"""Stone window frame and custom title bar for the Tkinter launcher."""
import os
import tkinter as tk
import random
import math
import sys
import ctypes
import ctypes.util
from PIL import Image, ImageDraw, ImageTk

STONE_DARK = '#393c3a'
STONE = '#626663'
STONE_LIGHT = '#929691'


class StoneWindowChrome:
    BORDER = 14
    TITLE_HEIGHT = 0

    def __init__(self, root, title, theme_provider, font, close, button_class=None):
        self.root = root
        self.title = title
        self.theme_provider = theme_provider
        self.font = font
        self.close = close
        self.maximized = False
        self.restore_geometry = None
        self.gesture = None
        self._size = None
        self._signature = None
        self._logo = None
        self._logo_photo = None
        self._background = None
        self._overlays = []
        self._shape_signature = None
        self._xdisplay = None
        self._restore_pending = None
        self._native_iconify = root.iconify
        self._native_deiconify = root.deiconify
        # Aqua must receive the borderless style before any font/layout update
        # maps the native window. Hide an already mapped root while changing it.
        if sys.platform == 'darwin':
            root.withdraw()
        root.resizable(False, False)
        root.overrideredirect(True)
        self._apply_macos_style()
        root.geometry('1128x728')  # Original 1100x700 content plus frame/title bar.
        root.minsize(888, 608)
        self.canvas = tk.Canvas(root, bg=STONE_DARK, bd=0, highlightthickness=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.content = tk.Frame(root, bg=theme_provider()['window'], bd=0)
        self.content.place(x=self.BORDER, y=self.BORDER+self.TITLE_HEIGHT,
                           relwidth=1, relheight=1, width=-2*self.BORDER,
                           height=-2*self.BORDER-self.TITLE_HEIGHT)
        self.controls = []
        if button_class is not None:
            owner = self
            class WindowControl(button_class):
                def __init__(self, action):
                    self._chrome_action = action
                    super().__init__(root, text='', command=lambda: owner._invoke(action),
                        font=font, padx=0, pady=0, theme_provider=theme_provider,
                        background_provider=owner._control_background, style='subtle')
                    self.configure(width=36, height=32)

                def redraw(self, event=None):
                    super().redraw(event)
                    owner._draw_icon(self, self.hovered, self.pressed)

            for action in ('minimize', 'close'):
                self.controls.append(WindowControl(action))
        else:
            for action in ('minimize', 'close'):
                control = tk.Canvas(root, width=36, height=32, bg=STONE_DARK,
                                    bd=0, highlightthickness=0, cursor='hand2', takefocus=True)
                control._chrome_action = action
                control._chrome_hover = False
                control._chrome_pressed = False
                control.bind('<Enter>', lambda e, c=control: self._hover(c, True))
                control.bind('<Leave>', lambda e, c=control: self._hover(c, False))
                control.bind('<ButtonPress-1>', lambda e, c=control: self._press_control(c))
                control.bind('<ButtonRelease-1>', lambda e, c=control: self._release_control(c, e))
                control.bind('<Return>', lambda e, a=action: self._invoke(a))
                control.bind('<space>', lambda e, a=action: self._invoke(a))
                self.controls.append(control)
        self.canvas.bind('<Configure>', self._configure)
        self.canvas.bind('<Motion>', self._motion)
        self.canvas.bind('<ButtonPress-1>', self._press)
        self.canvas.bind('<B1-Motion>', self._drag)
        self.canvas.bind('<ButtonRelease-1>', lambda e: setattr(self, 'gesture', None))
        self.canvas.bind('<Double-Button-1>', lambda e: "break")
        root.bind('<Alt-F4>', lambda e: self.close(), add='+')
        root.bind('<Map>', self._mapped, add='+')
        root.protocol('WM_DELETE_WINDOW', close)
        if sys.platform == 'darwin':
            self._native_deiconify()
        root.update_idletasks()
        if os.name == 'nt':
            self._apply_windows_taskbar()
        self._apply_window_shape()
        # Existing launch/tray preferences call these methods too.
        root.iconify = self.minimize
        root.deiconify = self.deiconify
        self.redraw()

    def _apply_macos_style(self):
        if sys.platform != 'darwin':
            return
        try:
            # Set Aqua's actual window style as well as Tk override-redirect.
            # This must be applied before the first map and after restoring.
            self.root.tk.call('::tk::unsupported::MacWindowStyle',
                              'style', self.root._w, 'plain', 'none')
        except tk.TclError:
            pass

    def _apply_windows_taskbar(self):
        """Use Tk's borderless mode; keep its native wrapper in the taskbar."""
        try:
            import ctypes
            from ctypes import wintypes
            api = ctypes.windll.user32
            api.GetParent.argtypes = [wintypes.HWND]
            api.GetParent.restype = wintypes.HWND
            hwnd = api.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            get_style = api.GetWindowLongPtrW if hasattr(api, 'GetWindowLongPtrW') else api.GetWindowLongW
            set_style = api.SetWindowLongPtrW if hasattr(api, 'SetWindowLongPtrW') else api.SetWindowLongW
            get_style.argtypes = [wintypes.HWND, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
            set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_style.restype = ctypes.c_ssize_t
            # Extended style only: do not fight Tk over caption/client geometry.
            style = get_style(hwnd, -20)
            updated = (style & ~0x00000080) | 0x00040000
            if updated != style:
                set_style(hwnd, -20, updated)
                api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
                api.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027)
        except (AttributeError, OSError, tk.TclError):
            pass

    def set_logo(self, image):
        if image is self._logo:
            return
        self._logo = image
        self._signature = None
        self.redraw()

    @staticmethod
    def _texture():
        rng = random.Random(43)
        tile = Image.new('RGB', (96, 96))
        tile.putdata([(v, v+2, v+1) for v in (rng.randrange(34, 53) for _ in range(96*96))])
        draw = ImageDraw.Draw(tile)
        for _ in range(14):
            x,y = rng.randrange(96),rng.randrange(96)
            draw.line([(x,y),(x+8,y+3),(x+15,y+2),(x+22,y+7)],fill='#1d211f')
            draw.line([(x,y+1),(x+8,y+4),(x+15,y+3)],fill='#505450')
        return tile

    def _configure(self, event):
        size = (event.width, event.height)
        if size != self._size:
            self._size = size
            self.redraw()

    def redraw(self):
        if not self.canvas.winfo_exists():
            return
        w,h = self.canvas.winfo_width(),self.canvas.winfo_height()
        if w < 30 or h < 30:
            return
        t = self.theme_provider()
        signature = (w,h,tuple(t.items()),id(self._logo))
        if signature == self._signature:
            return
        self._signature = signature
        c = self.canvas
        c.configure(bg='#080908')
        if self.content.cget('bg') != t['window']:
            self.content.configure(bg=t['window'])
        c.delete('frame')
        if not hasattr(self, '_stone_tile'):
            self._stone_tile = self._texture()
        frame = Image.new('RGB',(w,h),'#080908')
        for y in range(0,h,96):
            for x in range(0,w,96):
                frame.paste(self._stone_tile,(x,y))
        draw = ImageDraw.Draw(frame)
        cut = 11
        outline = [(cut,2),(w-cut-1,2),(w-3,cut),(w-3,h-cut-1),
                   (w-cut-1,h-3),(cut,h-3),(2,h-cut-1),(2,cut)]
        draw.polygon(outline,outline='#777b73')
        draw.line([(cut+1,4),(w-cut-2,4),(w-5,cut+1)],fill='#a0a097',width=1)
        draw.line([(w-5,cut+1),(w-5,h-cut-1),(w-cut-2,h-5),(cut,h-5)],fill='#151816',width=3)
        draw.rectangle((9,11,w-10,h-10),outline='#101310',width=3)
        # Small rivets along the inner stone rails.
        for x in range(25,w-20,45):
            for y in (8,h-8):
                draw.ellipse((x-1,y-1,x+2,y+2),fill='#151815')
                draw.point((x,y-1),fill='#92948a')
        for y in range(112,h-30,45):
            for x in (7,w-8):
                draw.ellipse((x-1,y-1,x+2,y+2),fill='#151815')
                draw.point((x,y-1),fill='#92948a')
        # Cut corners are opaque black, avoiding platform-dependent transparency.
        for points in ([(0,0),(cut,0),(0,cut)],[(w,0),(w-cut,0),(w,cut)],
                       [(0,h),(cut,h),(0,h-cut)],[(w,h),(w-cut,h),(w,h-cut)]):
            draw.polygon(points,fill='#080908')
        self._frame_photo = ImageTk.PhotoImage(frame,master=c)
        c.create_image(0,0,image=self._frame_photo,anchor='nw',tags='frame')
        for i,control in enumerate(self.controls):
            control.place(x=w-self.BORDER-12-(len(self.controls)-i)*38,y=self.BORDER+5,width=36,height=32)
            self._draw_control(control)
        self._apply_window_shape(w,h)

    def _apply_window_shape(self, width=None, height=None):
        """Clip the native window's cut corners, including scrims and popups."""
        width = width or self.root.winfo_width()
        height = height or self.root.winfo_height()
        cut = 11
        if os.name == 'nt':
            try:
                from ctypes import wintypes
                user = ctypes.windll.user32
                gdi = ctypes.windll.gdi32
                user.GetParent.argtypes = [wintypes.HWND]
                user.GetParent.restype = wintypes.HWND
                hwnd = user.GetParent(self.root.winfo_id()) or self.root.winfo_id()
                signature = (hwnd,width,height)
                if signature == self._shape_signature:
                    return
                points = (wintypes.POINT*8)(
                    (cut,0),(width-cut,0),(width,cut),(width,height-cut),
                    (width-cut,height),(cut,height),(0,height-cut),(0,cut))
                gdi.CreatePolygonRgn.argtypes = [ctypes.POINTER(wintypes.POINT),ctypes.c_int,ctypes.c_int]
                gdi.CreatePolygonRgn.restype = wintypes.HANDLE
                user.SetWindowRgn.argtypes = [wintypes.HWND,wintypes.HANDLE,wintypes.BOOL]
                user.SetWindowRgn.restype = ctypes.c_int
                gdi.DeleteObject.argtypes = [wintypes.HANDLE]
                region = gdi.CreatePolygonRgn(points,8,2)
                if region:
                    if user.SetWindowRgn(hwnd,region,True):
                        # Windows owns the region after a successful SetWindowRgn.
                        self._shape_signature = signature
                    else:
                        gdi.DeleteObject(region)
            except (AttributeError,OSError,tk.TclError):
                pass
        elif sys.platform.startswith('linux'):
            try:
                if self._xdisplay is None:
                    self._xlib = ctypes.CDLL(ctypes.util.find_library('X11'))
                    self._xshape = ctypes.CDLL(ctypes.util.find_library('Xext'))
                    self._xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
                    self._xlib.XOpenDisplay.restype = ctypes.c_void_p
                    self._xdisplay = self._xlib.XOpenDisplay(os.environ.get('DISPLAY','').encode())
                    if not self._xdisplay:
                        return
                    self.root.bind('<Destroy>',self._close_shape_display,add='+')
                window = self.root.winfo_id()
                signature = (window,width,height)
                if signature == self._shape_signature:
                    return
                class Rectangle(ctypes.Structure):
                    _fields_ = [('x',ctypes.c_short),('y',ctypes.c_short),
                                ('width',ctypes.c_ushort),('height',ctypes.c_ushort)]
                rows = [(cut-y,y,width-2*(cut-y),1) for y in range(cut)]
                rows.append((0,cut,width,height-2*cut))
                rows.extend((offset,height-offset,width-2*offset,1) for offset in range(cut,0,-1))
                rectangles = (Rectangle*len(rows))(*(Rectangle(*row) for row in rows))
                self._xshape.XShapeCombineRectangles.argtypes = [ctypes.c_void_p,ctypes.c_ulong,
                    ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.POINTER(Rectangle),ctypes.c_int,
                    ctypes.c_int,ctypes.c_int]
                targets = [window]
                parent,screen,children,count = ctypes.c_ulong(),ctypes.c_ulong(),ctypes.POINTER(ctypes.c_ulong)(),ctypes.c_uint()
                self._xlib.XQueryTree.argtypes = [ctypes.c_void_p,ctypes.c_ulong,
                    ctypes.POINTER(ctypes.c_ulong),ctypes.POINTER(ctypes.c_ulong),
                    ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),ctypes.POINTER(ctypes.c_uint)]
                if self._xlib.XQueryTree(self._xdisplay,window,ctypes.byref(screen),ctypes.byref(parent),ctypes.byref(children),ctypes.byref(count)):
                    if parent.value and parent.value != screen.value:
                        targets.append(parent.value)
                    if children:
                        self._xlib.XFree.argtypes = [ctypes.c_void_p]
                        self._xlib.XFree(children)
                for target in targets:
                    self._xshape.XShapeCombineRectangles(self._xdisplay,target,0,0,0,rectangles,len(rows),0,3)
                self._xlib.XFlush.argtypes = [ctypes.c_void_p]
                self._xlib.XFlush(self._xdisplay)
                self._shape_signature = signature
            except (AttributeError,OSError,TypeError,tk.TclError):
                pass

    def _close_shape_display(self, event):
        if event.widget is self.root and self._xdisplay:
            self._xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
            self._xlib.XCloseDisplay(self._xdisplay)
            self._xdisplay = None

    def set_background(self, frame):
        self._background = frame
        for control in self.controls:
            self._draw_control(control)

    def stone_border_image(self, width, height, points):
        if not hasattr(self, '_stone_tile'):
            self._stone_tile = self._texture()
        stone = Image.new('RGBA',(width,height))
        for y in range(0,height,96):
            for x in range(0,width,96):
                stone.paste(self._stone_tile,(x,y))
        mask = Image.new('L',(width,height),0)
        ImageDraw.Draw(mask).polygon(points,fill=255)
        stone.putalpha(mask)
        return stone

    def _control_background(self, control, width, height):
        image = Image.new('RGBA',(width,height),self.theme_provider()['window'])
        info = control.place_info()
        x,y = int(info.get('x',control.winfo_x())),int(info.get('y',control.winfo_y()))
        source = self._background
        if self._overlays:
            scrim = self._overlays[-1].scrim
            source = getattr(scrim,'_image',None)
            if source is None:
                return Image.new('RGBA',(width,height),str(scrim.cget('bg')))
        else:
            x,y = x-self.BORDER,y-self.BORDER
        if source is not None:
            # Avoid black padding from crop() while widgets finish mapping.
            left,top = max(0,x),max(0,y)
            right,bottom = min(source.width,x+width),min(source.height,y+height)
            if right>left and bottom>top:
                image.alpha_composite(source.crop((left,top,right,bottom)).convert('RGBA'),
                                      (left-x,top-y))
        return image

    def push_overlay(self, panel):
        self._overlays.append(panel)
        self.lift_controls()

    def pop_overlay(self, panel):
        self._overlays = [item for item in self._overlays if item is not panel and item.winfo_exists()]
        if getattr(getattr(self.root,'launcher',None),'_closing',False):
            return
        if not self._overlays and self.root.grab_current() is self.root:
            self.root.grab_release()
        self.lift_controls()

    def lift_controls(self):
        for control in self.controls:
            if control.winfo_exists():
                control.tk.call("raise", control._w)
                self._draw_control(control)

    def attach_drag_surface(self, surface):
        def press(event):
            if event.y < 42:
                self.gesture = ('',event.x_root,event.y_root,self.root.winfo_x(),
                                self.root.winfo_y(),self.root.winfo_width(),self.root.winfo_height())
        surface.bind('<ButtonPress-1>',press,add='+')
        surface.bind('<B1-Motion>',self._drag,add='+')
        surface.bind('<ButtonRelease-1>',lambda e:setattr(self,'gesture',None),add='+')
        surface.bind('<Double-Button-1>',lambda e:'break' if e.y<42 else None,add='+')

    def _draw_control(self, control):
        if hasattr(control,'redraw'):
            control.redraw()
            return
        control.delete('all')
        control._background_photo = ImageTk.PhotoImage(self._control_background(control,36,32),master=control)
        control.create_image(0,0,image=control._background_photo,anchor='nw')
        t=self.theme_provider()
        face=t['window'] if control._chrome_pressed else t['control_active'] if control._chrome_hover else t['panel']
        points=(7,4,29,4,32,7,32,25,29,28,7,28,4,25,4,7)
        control.create_polygon(points,fill=face,outline=t['accent'] if control._chrome_hover else t['border'])
        control.create_rectangle(8,8,28,24,outline=t['accent_dark'])
        self._draw_icon(control,control._chrome_hover,control._chrome_pressed)

    def _draw_icon(self, control, hovered, pressed):
        color=self.theme_provider()['bright' if hovered else 'accent']
        d=1 if pressed else 0
        action=control._chrome_action
        if action=='minimize':
            control.create_line(14+d,20+d,22+d,20+d,fill=color,width=2)
        elif action=='close':
            control.create_line(14+d,12+d,22+d,20+d,fill=color,width=2)
            control.create_line(22+d,12+d,14+d,20+d,fill=color,width=2)
        else:
            if self.maximized:
                control.create_rectangle(16+d,10+d,23+d,17+d,outline=color)
            control.create_rectangle(13+d,13+d,20+d,20+d,outline=color)

    def _hover(self, control, active):
        control._chrome_hover = active
        if not active:
            control._chrome_pressed = False
        self._draw_control(control)

    def _press_control(self, control):
        control.focus_set()
        control._chrome_pressed = True
        self._draw_control(control)

    def _release_control(self, control, event):
        pressed = control._chrome_pressed
        control._chrome_pressed = False
        self._draw_control(control)
        if pressed and 0 <= event.x < 36 and 0 <= event.y < 32:
            self._invoke(control._chrome_action)

    def _invoke(self, action):
        {'minimize': self.minimize, 'maximize': self.toggle_maximize, 'close': self.close}[action]()
        return 'break'

    def _edges(self, event):
        # The launcher retains its original fixed-size layout.
        return ''

    def _motion(self, event):
        edge = self._edges(event)
        cursor = {'n':'top_side','s':'bottom_side','w':'left_side','e':'right_side',
                  'nw':'top_left_corner','ne':'top_right_corner',
                  'sw':'bottom_left_corner','se':'bottom_right_corner'}.get(edge, '')
        try:
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            self.canvas.configure(cursor="sizing" if edge else "")

    def _press(self, event):
        edge = self._edges(event)
        if edge or event.y < self.BORDER+self.TITLE_HEIGHT:
            self.gesture = (edge, event.x_root, event.y_root, self.root.winfo_x(),
                            self.root.winfo_y(), self.root.winfo_width(), self.root.winfo_height())

    def _drag(self, event):
        if self.gesture is None:
            return
        edge, px, py, x, y, w, h = self.gesture
        dx, dy = event.x_root-px, event.y_root-py
        if not edge:
            if self.maximized:
                ratio = (px-x)/max(1,w)
                self.toggle_maximize()
                w, h = self.root.winfo_width(), self.root.winfo_height()
                x, y = event.x_root-int(w*ratio), event.y_root-24
                self.gesture = ('',event.x_root,event.y_root,x,y,w,h)
            else:
                x, y = x+dx, y+dy
            self.root.geometry(f'{x:+d}{y:+d}')
            return
        min_w, min_h = self.root.minsize()
        if 'e' in edge: w = max(min_w, w+dx)
        if 's' in edge: h = max(min_h, h+dy)
        if 'w' in edge:
            width = max(min_w, w-dx)
            x, w = x+w-width, width
        if 'n' in edge:
            height = max(min_h, h-dy)
            y, h = y+h-height, height
        self.root.geometry(f'{w}x{h}{x:+d}{y:+d}')

    def _double_click(self, event):
        if not self._edges(event) and event.y < self.BORDER+self.TITLE_HEIGHT:
            self.gesture = None
            self.toggle_maximize()

    def _work_area(self):
        if os.name == 'nt':
            try:
                import ctypes
                from ctypes import wintypes
                rect = wintypes.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):
                    return rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top
            except (AttributeError, OSError):
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def toggle_maximize(self):
        # Retained for compatibility with older callers; resizing is disabled.
        return

    def minimize(self):
        # Tk cannot iconify an override-redirect window. Restore management until
        # it is mapped again, then reapply the custom frame exactly once.
        self.root.overrideredirect(False)
        if sys.platform == 'darwin':
            # A plain Aqua window cannot be miniaturized. Restore the managed
            # style while hidden/minimized; _restore_chrome removes it on map.
            try:
                self.root.tk.call('::tk::unsupported::MacWindowStyle',
                                  'style', self.root._w, 'document', ())
            except tk.TclError:
                pass
        self._native_iconify()

    def deiconify(self):
        if sys.platform == 'darwin':
            self.root.overrideredirect(True)
            self._apply_macos_style()
        self._native_deiconify()
        self._schedule_restore()

    def _mapped(self, event):
        if event.widget is self.root:
            self._schedule_restore()

    def _schedule_restore(self):
        if self._restore_pending is None:
            self._restore_pending = self.root.after_idle(self._restore_chrome)

    def _restore_chrome(self):
        self._restore_pending = None
        if not self.root.winfo_exists() or self.root.state() != 'normal':
            return
        if not self.root.overrideredirect():
            self.root.overrideredirect(True)
        self._apply_macos_style()
        if os.name == 'nt':
            self._apply_windows_taskbar()
        self._apply_window_shape()
