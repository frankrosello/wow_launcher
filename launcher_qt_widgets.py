"""Native Qt widgets retaining the launcher's established layout API.

No Tcl interpreter or Tk widgets are created. Text is painted at the screen's
native device pixel ratio; existing layout and controller code remains reusable.
"""
from __future__ import annotations
import itertools, math, re, sys, traceback
from types import SimpleNamespace
from pathlib import Path
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from PIL import Image, ImageQt
from shiboken6 import isValid

NORMAL='normal'; DISABLED='disabled'; END='end'; UIError=RuntimeError
_app=None; _registry={}; _ids=itertools.count(1); _styles={}; _class_binds={}

def application():
    global _app
    if _app is None:
        _app=W.QApplication.instance() or W.QApplication(sys.argv[:1])
        _app.setStyle('Fusion')
    return _app

def color(v):
    return G.QColor(str(v or 'transparent'))

def qfont(value=None):
    if isinstance(value, Font): return G.QFont(value.qt)
    if isinstance(value, G.QFont): return G.QFont(value)
    f=G.QFont('Sans Serif',10)
    if isinstance(value,(tuple,list)) and value:
        f.setFamily(str(value[0])); size=int(value[1]) if len(value)>1 else 10
        if size<0: f.setPixelSize(-size)
        else: f.setPointSize(size)
        for opt in value[2:]:
            f.setBold('bold' in str(opt)); f.setItalic('italic' in str(opt))
    f.setHintingPreference(G.QFont.HintingPreference.PreferFullHinting)
    return f

class Font:
    def __init__(self,root=None,font=None,**kw):
        application(); self.qt=qfont(font); self.configure(**kw)
    def configure(self,**kw):
        if 'family' in kw:self.qt.setFamily(str(kw['family']))
        if 'size' in kw:
            size=int(kw['size']); self.qt.setPixelSize(-size) if size<0 else self.qt.setPointSize(size)
        if 'weight' in kw:self.qt.setBold(kw['weight']=='bold')
        if 'slant' in kw:self.qt.setItalic(kw['slant']=='italic')
        if 'underline' in kw:self.qt.setUnderline(bool(kw['underline']))
    config=configure
    def measure(self,text):return G.QFontMetrics(self.qt).horizontalAdvance(str(text))
    def metrics(self,key=None):
        m=G.QFontMetrics(self.qt); d={'linespace':m.height(),'ascent':m.ascent(),'descent':m.descent(),'fixed':self.qt.fixedPitch()}
        return d.get(key,0) if key else d
    def actual(self,key=None):
        d={'family':self.qt.family(),'size':self.qt.pointSize(),'weight':'bold' if self.qt.bold() else 'normal','slant':'italic' if self.qt.italic() else 'roman'}
        return d.get(key) if key else d

def families(root=None):application();return G.QFontDatabase.families()
qtfont=SimpleNamespace(Font=Font,families=families)

class Variable:
    def __init__(self,master=None,value=None,**kw):self.value=value;self.callbacks={};self._name='var'+str(next(_ids))
    def get(self):return self.value
    def set(self,value):
        if self.value==value:return
        self.value=value
        for cb in tuple(self.callbacks.values()):cb(self._name,'','write')
    def trace_add(self,mode,cb):key=str(next(_ids));self.callbacks[key]=cb;return key
    def trace_remove(self,mode,key):self.callbacks.pop(key,None)
    trace=trace_add
class StringVar(Variable):
    def __init__(self,master=None,value='',**kw):super().__init__(master,str(value),**kw)
class BooleanVar(Variable):
    def __init__(self,master=None,value=False,**kw):super().__init__(master,bool(value),**kw)
class IntVar(Variable):
    def __init__(self,master=None,value=0,**kw):super().__init__(master,int(value),**kw)

def _setit(variable,value,callback=None):
    def select(*args):
        variable.set(value)
        if callback:callback(value,*args)
    return select

class PhotoImage:
    def __init__(self,image=None,master=None,file=None,width=1,height=1,**kw):
        application()
        if file:image=Image.open(file)
        if image is None:image=Image.new('RGBA',(width,height))
        self.pil=image.copy();self.qt=G.QPixmap.fromImage(ImageQt.ImageQt(image.convert('RGBA')))
    def width(self):return self.qt.width()
    def height(self):return self.qt.height()
QtImage=SimpleNamespace(PhotoImage=PhotoImage)

class EventFilter(C.QObject):
    def __init__(self,owner):super().__init__(owner._qt);self.owner=owner
    def eventFilter(self,obj,event):
        o=self.owner
        if o._dead or not isValid(o._qt):return False
        t=event.type(); seq=[]; e=SimpleNamespace(widget=o,x=0,y=0,x_root=0,y_root=0,delta=0,num=0,width=o.winfo_width(),height=o.winfo_height(),keysym='',char='',state=0)
        modifiers=event.modifiers() if hasattr(event,'modifiers') else C.Qt.KeyboardModifier.NoModifier
        e.state=(1 if modifiers & C.Qt.KeyboardModifier.ShiftModifier else 0)|(4 if modifiers & (C.Qt.KeyboardModifier.ControlModifier|C.Qt.KeyboardModifier.MetaModifier) else 0)
        if hasattr(event,'position'):
            p=event.position();e.x=round(p.x());e.y=round(p.y())
        if hasattr(event,'globalPosition'):
            p=event.globalPosition();e.x_root=round(p.x());e.y_root=round(p.y())
        if t==C.QEvent.Type.Resize:
            seq=['<Configure>']
            if not getattr(o._root,'_layout_running',False):o._schedule_layout()
        elif t==C.QEvent.Type.Show:o._schedule_layout()
        elif t in (C.QEvent.Type.Enter,C.QEvent.Type.Leave):
            sequence='<Enter>' if t==C.QEvent.Type.Enter else '<Leave>'
            C.QTimer.singleShot(0,lambda:o.event_generate(sequence,**{k:v for k,v in vars(e).items() if k!='widget'}) if o.winfo_exists() else None)
            return False
        elif t==C.QEvent.Type.FocusIn:seq=['<FocusIn>']
        elif t==C.QEvent.Type.FocusOut:seq=['<FocusOut>']
        elif t==C.QEvent.Type.MouseMove:seq=['<Motion>']
        elif t in (C.QEvent.Type.MouseButtonPress,C.QEvent.Type.MouseButtonRelease,C.QEvent.Type.MouseButtonDblClick):
            e.num={C.Qt.MouseButton.LeftButton:1,C.Qt.MouseButton.MiddleButton:2,C.Qt.MouseButton.RightButton:3}.get(event.button(),1)
            stem='ButtonPress' if t==C.QEvent.Type.MouseButtonPress else 'ButtonRelease'
            seq=[f'<{stem}>',f'<{stem}-{e.num}>']
            if t==C.QEvent.Type.MouseButtonPress:
                seq+=[f'<Button-{e.num}>']
                if e.state & 4:seq.insert(0,f'<Control-Button-{e.num}>')
            if t==C.QEvent.Type.MouseButtonDblClick:seq=[f'<Double-{e.num}>',f'<Double-Button-{e.num}>']
        elif t==C.QEvent.Type.Wheel:
            e.delta=event.angleDelta().y() or event.pixelDelta().y()*8;e.num=4 if e.delta>0 else 5;seq=['<MouseWheel>',f'<Button-{e.num}>']
        elif t in (C.QEvent.Type.KeyPress,C.QEvent.Type.KeyRelease):
            key={C.Qt.Key.Key_Return:'Return',C.Qt.Key.Key_Enter:'Return',C.Qt.Key.Key_Escape:'Escape',C.Qt.Key.Key_Space:'space',C.Qt.Key.Key_Up:'Up',C.Qt.Key.Key_Down:'Down',C.Qt.Key.Key_Tab:'Tab'}.get(event.key(),event.text())
            if e.state & 4 and C.Qt.Key.Key_A<=event.key()<=C.Qt.Key.Key_Z:key=chr(event.key()).lower()
            e.keysym=key;e.char=event.text();stem='KeyPress' if t==C.QEvent.Type.KeyPress else 'KeyRelease';seq=[f'<{stem}>',f'<{stem}-{key}>']
            if t==C.QEvent.Type.KeyPress:
                seq.append(f'<{key}>')
                if e.state & 4:seq.insert(0,f'<Control-{key}>')
        elif t==C.QEvent.Type.Close:
            cb=o._protocols.get('WM_DELETE_WINDOW')
            if cb:cb();event.ignore();return True
        if isinstance(o,Treeview) and obj is o._qt.viewport():e.y+=o._qt.header().height()
        # Overlay modality without a second native window.
        modal=getattr(o._root,'_modal',None)
        if modal and modal.winfo_exists() and t in (C.QEvent.Type.MouseButtonPress,C.QEvent.Type.MouseButtonRelease,C.QEvent.Type.KeyPress):
            p=o
            while p is not None and p is not modal:p=p.master
            if p is None:return True
        try:
            for s in seq:
                for tag in o._bindtags:
                    cb=_class_binds.get((tag,s))
                    if cb and cb(e)=='break':return True
                for cb in tuple(o._bindings.get(s,())):
                    if cb(e)=='break':return True
            if isinstance(o,Canvas) and seq:o._item_events(seq,e)
        except Exception:traceback.print_exc()
        return False

class Widget:
    native=W.QWidget
    def __init__(self,master=None,**kw):
        application();self.master=master;self._root=master._root if master else self
        self._qt=self.native(master._qt if master else None);self._qt.setMouseTracking(True)
        self._w='qt'+str(next(_ids));self._qt.setObjectName(self._w);_registry[self._w]=self
        self.children={};self._bindings={};self._bindtags=(self._w,);self._protocols={};self._dead=False
        self._manager='';self._geometry={};self._opts={'bg':'#181614','fg':'#e8dfcb','font':('Sans Serif',10),'padx':0,'pady':0,'bd':0,'highlightthickness':0,'state':'normal'}
        self._grid_weights={'row':{},'column':{}};self._filter=EventFilter(self);self._qt.installEventFilter(self._filter)
        if hasattr(self._qt,'viewport'):self._qt.viewport().installEventFilter(self._filter);self._qt.viewport().setMouseTracking(True)
        if master:master.children[self._w]=self
        self._layout_pending=False;self.configure(**kw)
    def __str__(self):return self._w
    def configure(self,cnf=None,**kw):
        if cnf:kw.update(cnf)
        if not kw:return dict(self._opts)
        aliases={'background':'bg','foreground':'fg','borderwidth':'bd'}
        for k,v in kw.items():self._opts[aliases.get(k,k)]=v
        self._qt.setFont(qfont(self._opts.get('font')))
        self._qt.setEnabled(self._opts.get('state')!='disabled')
        if self._opts.get('takefocus'):self._qt.setFocusPolicy(C.Qt.FocusPolicy.StrongFocus)
        self._qt.setCursor(C.Qt.CursorShape.PointingHandCursor if self._opts.get('cursor')=='hand2' else C.Qt.CursorShape.ArrowCursor)
        self._apply()
        if any(k in kw for k in ('width','height','font','text','image','padx','pady','wraplength')):self._schedule_layout()
        self._qt.update()
    config=configure
    def _apply(self):
        p=self._qt.palette();p.setColor(G.QPalette.ColorRole.Window,color(self._opts.get('bg')));p.setColor(G.QPalette.ColorRole.WindowText,color(self._opts.get('fg')))
        p.setColor(G.QPalette.ColorRole.Base,color(self._opts.get('bg')));p.setColor(G.QPalette.ColorRole.Text,color(self._opts.get('fg')))
        p.setColor(G.QPalette.ColorRole.Highlight,color(self._opts.get('selectbackground',self._opts.get('activebackground','#473a27'))));p.setColor(G.QPalette.ColorRole.HighlightedText,color(self._opts.get('selectforeground','#fff1c7')))
        self._qt.setPalette(p);self._qt.setAutoFillBackground(True)
        if type(self._qt) is W.QWidget:
            edge=self._opts.get('highlightbackground',self._opts.get('bg'));bd=int(self._opts.get('highlightthickness',0)) or int(self._opts.get('bd',0))
            self._qt.setStyleSheet(f'QWidget#{self._w} {{border:{bd}px solid {edge};}}')
    def cget(self,key):return self._opts.get({'background':'bg','foreground':'fg'}.get(key,key),'')
    def __getitem__(self,key):return self.cget(key)
    def __setitem__(self,key,value):self.configure(**{key:value})
    def _manage(self,kind,kw):
        self._manager=kind;self._geometry.update(kw);self._qt.show()
        if self.master:self.master._schedule_layout()
    def pack(self,**kw):self._manage('pack',kw)
    def grid(self,**kw):self._manage('grid',kw)
    def place(self,**kw):self._manage('place',kw)
    place_configure=place;pack_configure=pack;grid_configure=grid
    def _forget(self):self._manager='';self._qt.hide();self.master and self.master._schedule_layout()
    pack_forget=_forget;grid_forget=_forget;place_forget=_forget;grid_remove=_forget
    def pack_info(self):return dict(self._geometry)
    place_info=pack_info;grid_info=pack_info
    def winfo_manager(self):return self._manager
    def _schedule_layout(self):
        if self._dead:return
        root=self._root
        if not root._layout_pending:
            root._layout_pending=True
            C.QTimer.singleShot(0,root._run_layout)
    def _run_layout(self):
        if self._dead:return
        self._layout_pending=False;self._layout_running=True
        self._layout_generation=getattr(self,'_layout_generation',0)+1
        try:self._layout()
        finally:self._layout_running=False
    def _get_request(self):
        generation=getattr(self._root,'_layout_generation',0)
        if getattr(self,'_request_generation',-1)!=generation:
            self._request_generation=generation;self._request_cache=self._request()
        return self._request_cache
    def _request(self):
        f=G.QFontMetrics(self._qt.font());w,h=1,1
        pack=[c for c in self.children.values() if c._manager=='pack'];grid=[c for c in self.children.values() if c._manager=='grid']
        for c in pack:
            cw,ch=c._get_request();px=_pair(c._geometry.get('padx',0));py=_pair(c._geometry.get('pady',0));cw+=sum(px)+2*int(c._geometry.get('ipadx',0));ch+=sum(py)+2*int(c._geometry.get('ipady',0))
            if c._geometry.get('side','top') in ('left','right'):w+=cw;h=max(h,ch)
            else:h+=ch;w=max(w,cw)
        if grid:
            cols,rows=self._grid_sizes(grid);w=max(w,sum(cols));h=max(h,sum(rows))
        w+=2*int(self._opts.get('padx',0));h+=2*int(self._opts.get('pady',0))
        return int(self._opts.get('width') or w),int(self._opts.get('height') or h)
    def _grid_sizes(self,children):
        ncol=max(int(c._geometry.get('column',0))+int(c._geometry.get('columnspan',1)) for c in children);nrow=max(int(c._geometry.get('row',0))+int(c._geometry.get('rowspan',1)) for c in children)
        cols=[0]*ncol;rows=[0]*nrow
        for c in children:
            g=c._geometry;col=int(g.get('column',0));row=int(g.get('row',0));cs=int(g.get('columnspan',1));rs=int(g.get('rowspan',1));w,h=c._get_request();w+=sum(_pair(g.get('padx',0)));h+=sum(_pair(g.get('pady',0)))
            if cs==1:cols[col]=max(cols[col],w)
            if rs==1:rows[row]=max(rows[row],h)
        for c in children:
            g=c._geometry;col=int(g.get('column',0));row=int(g.get('row',0));cs=int(g.get('columnspan',1));rs=int(g.get('rowspan',1));w,h=c._get_request()
            if cs>1 and w>sum(cols[col:col+cs]):
                extra=math.ceil((w-sum(cols[col:col+cs]))/cs)
                for i in range(col,col+cs):cols[i]+=extra
            if rs>1 and h>sum(rows[row:row+rs]):
                extra=math.ceil((h-sum(rows[row:row+rs]))/rs)
                for i in range(row,row+rs):rows[i]+=extra
        for sizes,axis in ((cols,'column'),(rows,'row')):
            groups={}
            for index,options in self._grid_weights[axis].items():
                if 0<=index<len(sizes):
                    sizes[index]=max(sizes[index],int(options.get('minsize',0)))
                    if options.get('uniform'):groups.setdefault(options['uniform'],[]).append(index)
            for indices in groups.values():
                unit=max(sizes[i]/max(1,self._grid_weights[axis][i].get('weight',1)) for i in indices)
                for i in indices:sizes[i]=math.ceil(unit*max(1,self._grid_weights[axis][i].get('weight',1)))
        return cols,rows
    def _layout(self):
        self._layout_pending=False
        if self._dead:return
        ox=int(self._opts.get('padx',0));oy=int(self._opts.get('pady',0));x,y=ox,oy;w=max(1,self.winfo_width()-2*ox);h=max(1,self.winfo_height()-2*oy)
        children=list(self.children.values());pack=[c for c in children if c._manager=='pack'];grid=[c for c in children if c._manager=='grid']
        vertical=sum(c._get_request()[1]+sum(_pair(c._geometry.get('pady',0))) for c in pack if c._geometry.get('side','top') in ('top','bottom'))
        horizontal=sum(c._get_request()[0]+sum(_pair(c._geometry.get('padx',0))) for c in pack if c._geometry.get('side','top') in ('left','right'))
        ve=sum(bool(c._geometry.get('expand')) for c in pack if c._geometry.get('side','top') in ('top','bottom'));he=sum(bool(c._geometry.get('expand')) for c in pack if c._geometry.get('side','top') in ('left','right'))
        vex=max(0,h-vertical)//max(1,ve);hex=max(0,w-horizontal)//max(1,he)
        for c in pack:
            g=c._geometry;side=g.get('side','top');cw,ch=c._get_request();cw+=2*int(g.get('ipadx',0));ch+=2*int(g.get('ipady',0));px=_pair(g.get('padx',0));py=_pair(g.get('pady',0));fill=g.get('fill','');expand=g.get('expand',False)
            if side in ('top','bottom'):
                ph=min(h,ch+sum(py)+(vex if expand else 0));pw=w;xx=x;yy=y if side=='top' else y+h-ph
                if side=='top':y+=ph
                h-=ph
            else:
                pw=min(w,cw+sum(px)+(hex if expand else 0));ph=h;xx=x if side=='left' else x+w-pw;yy=y
                if side=='left':x+=pw
                w-=pw
            aw=max(1,pw-sum(px));ah=max(1,ph-sum(py));dw=aw if fill in ('both','x') else min(cw,aw);dh=ah if fill in ('both','y') else min(ch,ah)
            dx,dy=_anchor(g.get('anchor','center'),aw-dw,ah-dh);c._set_rect(xx+px[0]+dx,yy+py[0]+dy,dw,dh)
        if grid:
            cols,rows=self._grid_sizes(grid);gw=max(1,self.winfo_width()-2*ox);gh=max(1,self.winfo_height()-2*oy)
            for sizes,axis,available in ((cols,'column',gw),(rows,'row',gh)):
                weights=[self._grid_weights[axis].get(i,{}).get('weight',0) for i in range(len(sizes))];total=sum(weights);extra=available-sum(sizes)
                if total:
                    for i,weight in enumerate(weights):sizes[i]=max(1,sizes[i]+round(extra*weight/total))
            for c in grid:
                g=c._geometry;col=int(g.get('column',0));row=int(g.get('row',0));cs=int(g.get('columnspan',1));rs=int(g.get('rowspan',1));px=_pair(g.get('padx',0));py=_pair(g.get('pady',0));cw,ch=c._get_request();aw=max(1,sum(cols[col:col+cs])-sum(px));ah=max(1,sum(rows[row:row+rs])-sum(py));st=g.get('sticky','');dw=aw if 'e' in st and 'w' in st else min(cw,aw);dh=ah if 'n' in st and 's' in st else min(ch,ah);anchor=('n' if 'n' in st else 's' if 's' in st else '')+('w' if 'w' in st else 'e' if 'e' in st else '');dx,dy=_anchor(anchor or 'center',aw-dw,ah-dh)
                c._set_rect(ox+sum(cols[:col])+px[0]+dx,oy+sum(rows[:row])+py[0]+dy,dw,dh)
        for c in children:
            if c._manager!='place':continue
            g=c._geometry;cw,ch=c._get_request();pw=self.winfo_width();ph=self.winfo_height();dw=int(g.get('width',0))+round(float(g['relwidth'])*pw) if 'relwidth' in g else int(g.get('width',cw));dh=int(g.get('height',0))+round(float(g['relheight'])*ph) if 'relheight' in g else int(g.get('height',ch));xx=int(g.get('x',0))+round(float(g.get('relx',0))*pw);yy=int(g.get('y',0))+round(float(g.get('rely',0))*ph);dx,dy=_anchor(g.get('anchor','nw'),dw,dh);c._set_rect(xx-dx,yy-dy,dw,dh)
    def _set_rect(self,x,y,w,h):
        r=C.QRect(round(x),round(y),max(1,round(w)),max(1,round(h)))
        if self._qt.geometry()!=r:self._qt.setGeometry(r)
        self._layout()
    def columnconfigure(self,index,**kw):self._grid_weights['column'][int(index)]=kw;self._schedule_layout()
    def rowconfigure(self,index,**kw):self._grid_weights['row'][int(index)]=kw;self._schedule_layout()
    grid_columnconfigure=columnconfigure;grid_rowconfigure=rowconfigure
    def pack_propagate(self,value):pass
    grid_propagate=pack_propagate
    def bind(self,sequence,cb,add=None):
        if not add:self._bindings[sequence]=[]
        self._bindings.setdefault(sequence,[]).append(cb);return str(id(cb))
    def unbind(self,sequence,funcid=None):self._bindings.pop(sequence,None)
    def bindtags(self,tags=None):
        if tags is not None:self._bindtags=tuple(tags)
        return self._bindtags
    def bind_class(self,tag,seq,cb):_class_binds[(tag,seq)]=cb
    def unbind_class(self,tag,seq):_class_binds.pop((tag,seq),None)
    def bind_all(self,seq,cb,add=None):self._root.bind(seq,cb,add)
    def unbind_all(self,seq):self._root.unbind(seq)
    def event_generate(self,seq,**kw):
        e=SimpleNamespace(widget=self,**kw)
        for cb in tuple(self._bindings.get(seq,())):cb(e)
    def after(self,ms,cb=None,*args):
        if cb is None:return
        timer=C.QTimer(self._root._qt);timer.setSingleShot(True);key='timer'+str(next(_ids));self._root._timers[key]=timer
        def run():
            self._root._timers.pop(key,None);timer.deleteLater()
            if not self._dead:
                try:cb(*args)
                except Exception:traceback.print_exc()
        timer.timeout.connect(run);timer.start(max(0,int(ms)));return key
    def after_idle(self,cb,*args):return self.after(0,cb,*args)
    def after_cancel(self,key):
        t=self._root._timers.pop(key,None)
        if t:t.stop();t.deleteLater()
    def update_idletasks(self):self._root._run_layout();application().processEvents(C.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    def update(self):application().processEvents()
    def winfo_width(self):return self._qt.width()
    def winfo_height(self):return self._qt.height()
    def winfo_reqwidth(self):return self._request()[0]
    def winfo_reqheight(self):return self._request()[1]
    def winfo_x(self):return self._qt.x()
    def winfo_y(self):return self._qt.y()
    def winfo_rootx(self):return self._qt.mapToGlobal(C.QPoint()).x()
    def winfo_rooty(self):return self._qt.mapToGlobal(C.QPoint()).y()
    def winfo_screenwidth(self):return application().primaryScreen().availableGeometry().width()
    def winfo_screenheight(self):return application().primaryScreen().availableGeometry().height()
    def winfo_exists(self):return not self._dead
    def winfo_children(self):return list(self.children.values())
    def winfo_toplevel(self):return self._root
    def winfo_ismapped(self):return not self._dead and self._qt.isVisible()
    winfo_viewable=winfo_ismapped
    def winfo_rgb(self,c):q=color(c);return q.red()*257,q.green()*257,q.blue()*257
    def winfo_pointerxy(self):p=G.QCursor.pos();return p.x(),p.y()
    def winfo_containing(self,x,y):
        q=application().widgetAt(int(x),int(y))
        while q:
            for w in _registry.values():
                if w._qt is q:return w
            q=q.parentWidget()
    def focus_set(self):self._qt.setFocus()
    focus_force=focus_set
    def focus_displayof(self):return self.focus_get()
    def clipboard_clear(self):application().clipboard().clear()
    def clipboard_append(self,text):application().clipboard().setText(application().clipboard().text()+str(text))
    def focus_get(self):
        q=application().focusWidget();return next((w for w in _registry.values() if w._qt is q),None)
    def lift(self,above=None):self._qt.raise_()
    def lower(self,below=None):self._qt.lower()
    def grab_set(self):self._previous_modal=getattr(self._root,'_modal',None);self._root._modal=self
    def grab_release(self):
        if getattr(self._root,'_modal',None) is self:
            previous=getattr(self,'_previous_modal',None);self._root._modal=previous if previous and previous.winfo_exists() else None
    def protocol(self,name,cb):self._protocols[name]=cb
    def wait_variable(self,var):
        loop=C.QEventLoop();token=var.trace_add('write',lambda *a:loop.quit());loop.exec();var.trace_remove('write',token)
    def destroy(self):
        if self._dead:return
        self.event_generate('<Destroy>');self.grab_release()
        for child in tuple(self.children.values()):Widget.destroy(child)
        self._dead=True;self._qt.hide();self._qt.deleteLater();_registry.pop(self._w,None)
        if self.master:self.master.children.pop(self._w,None);self.master._schedule_layout()
    def register(self,cb):return cb
    def deletecommand(self,cb):pass

def _pair(v):
    if isinstance(v,(tuple,list)):return (int(v[0]),int(v[-1]))
    return int(v),int(v)
def _anchor(a,w,h):
    if a in ('center','c',''):return w/2,h/2
    return (0 if 'w' in a else w if 'e' in a else w/2,0 if 'n' in a else h if 's' in a else h/2)

class Window(Widget):
    def __init__(self,**kw):
        super().__init__(None,**kw);self._timers={};self._modal=None;self._qt.resize(1100,700);self._qt.show()
    def title(self,text):self._qt.setWindowTitle(text)
    def geometry(self,s):
        match=re.match(r'(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?',s)
        if match:
            self._qt.resize(int(match[1]),int(match[2]))
            if match[3]:self._qt.move(int(match[3]),int(match[4]))
    def minsize(self,w,h):self._qt.setMinimumSize(w,h)
    def maxsize(self,w,h):self._qt.setMaximumSize(w,h)
    def resizable(self,x,y):
        # Preserve requested launcher size; overlays themselves remain responsive.
        if not x and not y:self._qt.setFixedSize(self._qt.size())
    def mainloop(self):self._layout();return application().exec()
    def quit(self):application().quit()
    def destroy(self):
        for timer in self._timers.values():timer.stop()
        super().destroy();application().quit()
    def withdraw(self):self._qt.hide()
    def deiconify(self):self._qt.showNormal()
    def iconify(self):self._qt.showMinimized()
    def iconphoto(self,default,*photos):
        if photos:self._qt.setWindowIcon(G.QIcon(photos[0].qt))
    def iconbitmap(self,path):self._qt.setWindowIcon(G.QIcon(path))
    def attributes(self,name,*value):
        if name=='-alpha':
            if value:self._qt.setWindowOpacity(float(value[0]))
            return self._qt.windowOpacity()
        if name=='-topmost' and value:self._qt.setWindowFlag(C.Qt.WindowType.WindowStaysOnTopHint,bool(value[0]))
    def state(self,*value):return 'iconic' if self._qt.isMinimized() else 'normal'

class Frame(Widget):pass
class Label(Widget):
    native=W.QLabel
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw)
        self._variable=kw.get('textvariable')
        if self._variable:
            self.configure(text=self._variable.get());self._variable.trace_add('write',lambda *a:self.configure(text=self._variable.get()) if self.winfo_exists() else None)
    def _apply(self):
        super()._apply();o=self._opts;self._qt.setText(str(o.get('text','')));self._qt.setWordWrap(bool(o.get('wraplength')))
        a=o.get('anchor','center');a='' if a=='center' else a;alignment=C.Qt.AlignmentFlag.AlignLeft if 'w' in a else C.Qt.AlignmentFlag.AlignRight if 'e' in a else C.Qt.AlignmentFlag.AlignHCenter
        alignment|=C.Qt.AlignmentFlag.AlignTop if 'n' in a else C.Qt.AlignmentFlag.AlignBottom if 's' in a else C.Qt.AlignmentFlag.AlignVCenter;self._qt.setAlignment(alignment)
        if o.get('image'):self._qt.setPixmap(o['image'].qt)
        self._qt.setMargin(int(o.get('padx',0)))
        bd=int(o.get('highlightthickness',0)) or int(o.get('bd',0));self._qt.setStyleSheet(f'QLabel#{self._w} {{border:{bd}px solid {o.get("highlightbackground",o["bg"])};}}')
    def _request(self):
        o=self._opts;fm=G.QFontMetrics(self._qt.font());text=str(o.get('text',''));wrap=int(o.get('wraplength') or 0);w=max((fm.horizontalAdvance(l) for l in text.split('\n')),default=0);h=max(1,len(text.split('\n')))*fm.height()
        if wrap and w>wrap:
            r=fm.boundingRect(C.QRect(0,0,wrap,100000),int(C.Qt.TextFlag.TextWordWrap),text);w=wrap;h=r.height()
        if o.get('image'):w=o['image'].width();h=o['image'].height()
        if o.get('width'):w=int(o['width'])*fm.horizontalAdvance('0')
        if o.get('height'):h=int(o['height'])*fm.height()
        return w+2*int(o.get('padx',0))+2,h+2*int(o.get('pady',0))+2

class Entry(Widget):
    native=W.QLineEdit
    def __init__(self,master=None,**kw):
        self._variable=kw.get('textvariable');super().__init__(master,**kw)
        if self._variable:
            self._qt.setText(str(self._variable.get()));self._trace=self._variable.trace_add('write',lambda *a:self._qt.setText(str(self._variable.get())) if self.winfo_exists() and self._qt.text()!=str(self._variable.get()) else None)
        self._qt.textChanged.connect(lambda t:self._variable.set(t) if self._variable else None)
    def _apply(self):
        super()._apply();o=self._opts;bd=max(int(o.get('highlightthickness',0)),int(o.get('bd',0)));edge=o.get('highlightbackground',o.get('bg'));focus=o.get('highlightcolor',edge)
        self._qt.setStyleSheet(f'QLineEdit {{background:{o["bg"]};color:{o["fg"]};border:{bd}px solid {edge};padding:3px;}} QLineEdit:focus {{border-color:{focus};}}')
        if o.get('show'):self._qt.setEchoMode(W.QLineEdit.EchoMode.Password)
    def _request(self):fm=G.QFontMetrics(self._qt.font());return int(self._opts.get('width',20))*fm.horizontalAdvance('0')+12,fm.height()+12
    def get(self):return self._qt.text()
    def delete(self,start,end=None):
        text=self.get();s=int(start);e=len(text) if end=='end' else int(end) if end is not None else s+1;self._qt.setText(text[:s]+text[e:])
    def insert(self,index,text):
        cur=self.get();i=len(cur) if index=='end' else int(index);self._qt.setText(cur[:i]+str(text)+cur[i:])
    def selection_range(self,a,b):self._qt.setSelection(int(a),len(self.get())-int(a) if b=='end' else int(b)-int(a))
    def icursor(self,i):self._qt.setCursorPosition(len(self.get()) if i=='end' else int(i))

class Button(Widget):
    native=W.QPushButton
    def __init__(self,master=None,**kw):super().__init__(master,**kw);self._qt.clicked.connect(self.invoke)
    def _apply(self):
        super()._apply();o=self._opts;self._qt.setText(str(o.get('text','')));self._qt.setStyleSheet(f'QPushButton {{background:{o["bg"]};color:{o["fg"]};border:1px solid {o.get("highlightbackground",o["bg"])};padding:5px;}} QPushButton:hover {{background:{o.get("activebackground",o["bg"])};}}')
    def invoke(self,*args):
        if self._opts.get('state')!='disabled' and self._opts.get('command'):return self._opts['command']()
    def _request(self):s=self._qt.sizeHint();return s.width(),s.height()
class Checkbutton(Button):
    native=W.QCheckBox
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw);var=kw.get('variable');self._variable=var
        if var:self._qt.setChecked(bool(var.get()));var.trace_add('write',lambda *a:self._qt.setChecked(bool(var.get())))
        self._qt.toggled.connect(lambda v:var.set(v) if var else None)
    def _apply(self):
        Widget._apply(self);o=self._opts;self._qt.setText(str(o.get('text','')));self._qt.setStyleSheet(f'QCheckBox {{background:{o["bg"]};color:{o["fg"]};spacing:8px;}} QCheckBox::indicator {{width:14px;height:14px;background:{o.get("selectcolor",o["bg"])};border:1px solid {o.get("activeforeground",o["fg"])};}} QCheckBox::indicator:checked {{background:{o.get("activeforeground",o["fg"])};}}')
class Radiobutton(Checkbutton):
    native=W.QRadioButton
    def __init__(self,master=None,**kw):
        # radio variables carry strings rather than boolean values
        Button.__init__(self,master,**kw);var=kw.get('variable');value=kw.get('value');self._qt.setAutoExclusive(False)
        if var:self._qt.setChecked(var.get()==value);var.trace_add('write',lambda *a:self._qt.setChecked(var.get()==value));self._qt.clicked.connect(lambda:var.set(value))

class Menu:
    def __init__(self,master=None,**kw):self.master=master;self.entries=[];self.opts=kw
    def winfo_exists(self):return self.master is None or self.master.winfo_exists()
    def add_command(self,**kw):self.entries.append({'state':'normal',**kw});self._changed()
    def add_separator(self):self.entries.append({'separator':True});self._changed()
    def add_cascade(self,**kw):self.entries.append(kw);self._changed()
    def _changed(self):
        if hasattr(self.master,'_refresh_menu'):self.master._refresh_menu()
    def index(self,i):return len(self.entries)-1 if i=='end' and self.entries else None if i=='end' else int(i)
    def entrycget(self,i,k):return self.entries[int(i)].get(k,'')
    def entryconfigure(self,i,**kw):self.entries[int(i)].update(kw);self._changed()
    entryconfig=entryconfigure
    def configure(self,**kw):self.opts.update(kw);self._changed()
    config=configure
    def invoke(self,i):
        item=self.entries[int(i)]
        if item.get('state')!='disabled' and item.get('command'):return item['command']()
    def delete(self,start,end=None):
        end=self.index(end) if end is not None else int(start)
        if end is not None:del self.entries[int(start):end+1]
        self._changed()
    def popup(self,x,y):
        menu=W.QMenu(self.master._qt);menu.setStyleSheet(f'QMenu {{background:{self.opts.get("bg","#211d1b")};color:{self.opts.get("fg","#e8dfcb")};}}')
        for i,e in enumerate(self.entries):
            if e.get('separator'):menu.addSeparator();continue
            action=menu.addAction(e.get('label',''));action.setEnabled(e.get('state')!='disabled');action.triggered.connect(lambda checked=False,index=i:self.invoke(index))
        menu.exec(C.QPoint(int(x),int(y)))
    def grab_release(self):pass

class DisabledChoiceFilter(C.QObject):
    def __init__(self,combo):super().__init__(combo);self.combo=combo
    def eventFilter(self,watched,event):
        if event.type() in (C.QEvent.Type.MouseButtonPress,C.QEvent.Type.MouseButtonRelease,C.QEvent.Type.MouseButtonDblClick):
            index=self.combo.view().indexAt(event.position().toPoint())
            if index.isValid() and not index.flags() & C.Qt.ItemFlag.ItemIsEnabled:
                event.accept();return True
        return False

class NativeCombo(W.QComboBox):
    def __init__(self,parent=None):
        super().__init__(parent);self._disabled_filter=DisabledChoiceFilter(self)
    def showPopup(self):
        super().showPopup()
        self.view().viewport().removeEventFilter(self._disabled_filter)
        self.view().viewport().installEventFilter(self._disabled_filter)

    def paintEvent(self,event):
        super().paintEvent(event)
        p=G.QPainter(self);p.setRenderHint(G.QPainter.RenderHint.Antialiasing);p.setPen(C.Qt.PenStyle.NoPen);p.setBrush(self.palette().color(G.QPalette.ColorRole.Text));x=self.width()-15;y=self.height()/2
        p.drawPolygon(G.QPolygonF([C.QPointF(x-4,y-2),C.QPointF(x+4,y-2),C.QPointF(x,y+3)]))

class ComboBox(Widget):
    native=NativeCombo
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw);self._variable=kw.get('textvariable')
        if self._variable:self._variable.trace_add('write',lambda *a:self.set(self._variable.get()) if self.winfo_exists() else None);self.set(self._variable.get())
        self._qt.activated.connect(self._selected)
    def _apply(self):
        super()._apply();o=self._opts
        if 'values' in o:
            old=self._qt.currentText();self._qt.blockSignals(True);self._qt.clear();self._qt.addItems([str(v) for v in o['values']]);self._qt.setCurrentText(old);self._qt.blockSignals(False)
        bg=o.get('bg','#20313d');fg=o.get('fg','#e8dfcb');border=o.get('bordercolor','#435360');active=o.get('activebackground','#2d4655')
        self._qt.setStyleSheet(f'QComboBox {{background:{bg};color:{fg};border:1px solid {border};padding:4px 28px 4px 8px;}} QComboBox::drop-down {{border:none;width:22px;}} QComboBox::down-arrow {{image:none;}} QComboBox QAbstractItemView {{background:{bg};color:{fg};border:1px solid {border};selection-background-color:{active};outline:none;}}')
        self._qt.setEditable(False)
    def _request(self):s=self._qt.sizeHint();return max(120,s.width()),max(28,s.height())
    def _selected(self,index):
        if self._variable:self._variable.set(self._qt.itemText(index))
        self.event_generate('<<ComboboxSelected>>')
    def current(self,index=None):
        if index is not None:self._qt.setCurrentIndex(index)
        return self._qt.currentIndex()
    def set(self,value):
        value=str(value);index=self._qt.findText(value)
        self._qt.setCurrentIndex(index)
        if index<0:self._qt.setPlaceholderText(value)
    def get(self):return self._qt.currentText()

class GameDropdown(ComboBox):
    def __init__(self,master,variable,value,*values,command=None):
        self.variable=variable;self._menu=Menu(self);super().__init__(master,textvariable=variable)
        for item in (value,*values):self._menu.add_command(label=item,command=_setit(variable,item,command))
        self.set(variable.get())
    def __getitem__(self,key):return self._menu if key=='menu' else super().__getitem__(key)
    def _refresh_menu(self):
        if not hasattr(self,'_qt'):return
        self._qt.blockSignals(True);old=self.variable.get();self._qt.clear()
        for i,e in enumerate(self._menu.entries):
            self._qt.addItem(str(e.get('label','')));item=self._qt.model().item(i);item.setEnabled(e.get('state')!='disabled')
            if e.get('foreground'):item.setForeground(color(e['foreground']))
        self.set(old);self._qt.blockSignals(False)
    def _selected(self,index):self._menu.invoke(index);self.set(self.variable.get())

class PaintCanvas(W.QWidget):
    def paintEvent(self,event):
        owner=getattr(self,'owner',None)
        if owner:owner._paint()
class Canvas(Widget):
    native=PaintCanvas
    def __init__(self,master=None,**kw):self.items={};self._itemids=itertools.count(1);self._offset=[0.,0.];self._windows={};self._tagbinds={};self._hover=None;super().__init__(master,**kw);self._qt.owner=self
    def _layout(self):
        super()._layout()
        if hasattr(self,'_windows'):self._update_windows()
    def _create(self,kind,coords,kw):
        if len(coords)==1 and isinstance(coords[0],(list,tuple)):coords=coords[0]
        i=next(self._itemids);self.items[i]={'kind':kind,'coords':list(coords),**kw};self._qt.update();return i
    def create_rectangle(self,*p,**kw):return self._create('rectangle',p,kw)
    def create_polygon(self,*p,**kw):return self._create('polygon',p,kw)
    def create_line(self,*p,**kw):return self._create('line',p,kw)
    def create_oval(self,*p,**kw):return self._create('oval',p,kw)
    def create_arc(self,*p,**kw):return self._create('arc',p,kw)
    def create_text(self,*p,**kw):return self._create('text',p,kw)
    def create_image(self,*p,**kw):return self._create('image',p,kw)
    def create_window(self,*p,**kw):
        i=self._create('window',p,kw);self._windows[i]=kw['window'];self._update_windows();return i
    def _match(self,key):
        if key=='all':return list(self.items)
        if isinstance(key,int) or str(key).isdigit():return [int(key)] if int(key) in self.items else []
        return [i for i,v in self.items.items() if key in self._tags(v)]
    def _tags(self,v):t=v.get('tags',v.get('tag',()));return (t,) if isinstance(t,str) else tuple(t)
    def delete(self,*keys):
        for key in keys:
            for i in self._match(key):self.items.pop(i,None);self._windows.pop(i,None)
        self._qt.update()
    def coords(self,key,*p):
        ids=self._match(key)
        if not ids:return []
        if p:self.items[ids[0]]['coords']=list(p[0] if len(p)==1 and isinstance(p[0],(tuple,list)) else p);self._update_windows();self._qt.update()
        return self.items[ids[0]]['coords']
    def itemconfigure(self,key,**kw):
        for i in self._match(key):self.items[i].update(kw)
        self._update_windows();self._qt.update()
    itemconfig=itemconfigure
    def itemcget(self,key,opt):ids=self._match(key);return self.items[ids[0]].get(opt,'') if ids else ''
    def find_withtag(self,tag):return tuple(self._match(tag))
    def find_all(self):return tuple(self.items)
    def type(self,i):return self.items[i]['kind']
    def move(self,key,dx,dy):
        for i in self._match(key):self.items[i]['coords']=[v+(dx if j%2==0 else dy) for j,v in enumerate(self.items[i]['coords'])]
        self._update_windows();self._qt.update()
    def tag_lower(self,key,below=None):
        for i in self._match(key):v=self.items.pop(i);self.items={i:v,**self.items}
        self._qt.update()
    def tag_raise(self,key,above=None):
        for i in self._match(key):v=self.items.pop(i);self.items[i]=v
        self._qt.update()
    def bbox(self,key):
        rects=[self._bounds(self.items[i]) for i in self._match(key)]
        if not rects:return None
        r=rects[0]
        for item in rects[1:]:r=r.united(item)
        return (math.floor(r.left()),math.floor(r.top()),math.ceil(r.right()),math.ceil(r.bottom()))
    def _bounds(self,item):
        p=item['coords'];kind=item['kind']
        if kind in ('text','image','window'):
            if kind=='image':image=item.get('image');w,h=(image.width(),image.height()) if image else (1,1)
            elif kind=='window':w,h=item['window']._get_request();w=item.get('width',w);h=item.get('height',h)
            else:
                fm=G.QFontMetrics(qfont(item.get('font')));width=int(item.get('width') or 100000);r=fm.boundingRect(C.QRect(0,0,width,100000),int(C.Qt.TextFlag.TextWordWrap) if item.get('width') else 0,str(item.get('text','')));w,h=r.width(),r.height()
            dx,dy=_anchor(item.get('anchor','center'),w,h);return C.QRectF(p[0]-dx,p[1]-dy,w,h)
        xs=p[::2];ys=p[1::2];return C.QRectF(min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys))
    def _paint(self):
        painter=G.QPainter(self._qt);painter.setRenderHint(G.QPainter.RenderHint.Antialiasing);painter.translate(-self._offset[0],-self._offset[1])
        for item in tuple(self.items.values()):
            if item.get('state')=='hidden':continue
            kind=item['kind'];p=item['coords'];r=self._bounds(item);fill=item.get('fill','');outline=item.get('outline','');pen=G.QPen(color(outline or fill),float(item.get('width',1)) if kind!='text' else 1)
            if kind!='line' and not outline:pen=G.QPen(C.Qt.PenStyle.NoPen)
            if item.get('dash'):pen.setStyle(C.Qt.PenStyle.DashLine)
            painter.setPen(pen);painter.setBrush(G.QBrush(color(fill)) if fill else C.Qt.BrushStyle.NoBrush)
            if kind=='rectangle':painter.drawRect(r)
            elif kind=='oval':painter.drawEllipse(r)
            elif kind=='arc':
                painter.drawArc(r,round(float(item.get('start',0))*16),round(float(item.get('extent',90))*16))
            elif kind in ('line','polygon'):
                poly=G.QPolygonF([C.QPointF(p[i],p[i+1]) for i in range(0,len(p),2)])
                painter.drawPolyline(poly) if kind=='line' else painter.drawPolygon(poly)
            elif kind=='image' and item.get('image'):painter.drawPixmap(r.topLeft(),item['image'].qt)
            elif kind=='text':
                painter.setFont(qfont(item.get('font')));painter.setPen(color(fill));flags=C.Qt.AlignmentFlag.AlignLeft if item.get('justify')=='left' else C.Qt.AlignmentFlag.AlignRight if item.get('justify')=='right' else C.Qt.AlignmentFlag.AlignHCenter
                flags|=C.Qt.AlignmentFlag.AlignVCenter
                if item.get('width'):flags|=C.Qt.TextFlag.TextWordWrap
                painter.drawText(r,flags,str(item.get('text','')))
        painter.end()
    def _update_windows(self):
        for i,w in self._windows.items():
            if w._dead:continue
            r=self._bounds(self.items[i]);w._set_rect(r.x()-self._offset[0],r.y()-self._offset[1],r.width(),r.height());w._qt.show()
    def tag_bind(self,tag,seq,cb):self._tagbinds[(tag,seq)]=cb
    def _item_events(self,seq,event):
        pos=C.QPointF(event.x+self._offset[0],event.y+self._offset[1]);hit=next((i for i,v in reversed(tuple(self.items.items())) if self._bounds(v).contains(pos)),None)
        if '<Motion>' in seq or '<Leave>' in seq:
            if '<Leave>' in seq:hit=None
            if hit!=self._hover:
                for item,ev in ((self._hover,'<Leave>'),(hit,'<Enter>')):
                    if item in self.items:
                        for tag in (item,*self._tags(self.items[item])):
                            cb=self._tagbinds.get((tag,ev))
                            if cb:cb(event)
                self._hover=hit
        if hit in self.items:
            for tag in (hit,*self._tags(self.items[hit])):
                for s in seq:
                    cb=self._tagbinds.get((tag,s))
                    if cb:cb(event)
    def canvasx(self,x):return float(x)+self._offset[0]
    def canvasy(self,y):return float(y)+self._offset[1]
    def yview(self,*args):return self._view(1,args)
    def xview(self,*args):return self._view(0,args)
    def _view(self,axis,args):
        region=self._opts.get('scrollregion') or self.bbox('all') or (0,0,1,1);size=max(1,float(region[axis+2])-float(region[axis]));viewport=self.winfo_height() if axis else self.winfo_width()
        if args:
            if args[0]=='moveto':self._offset[axis]=float(args[1])*size
            else:self._offset[axis]+=int(args[1])*(20 if args[2]=='units' else viewport*.9)
            self._offset[axis]=max(0,min(max(0,size-viewport),self._offset[axis]));self._update_windows();self._qt.update()
            cb=self._opts.get('yscrollcommand' if axis else 'xscrollcommand')
            if cb:cb(self._offset[axis]/size,min(1,(self._offset[axis]+viewport)/size))
        return self._offset[axis]/size,min(1,(self._offset[axis]+viewport)/size)
    def yview_moveto(self,f):return self.yview('moveto',f)
    def yview_scroll(self,n,what):return self.yview('scroll',n,what)

class Scrollbar(Widget):
    native=W.QScrollBar
    def __init__(self,master=None,**kw):super().__init__(master,**kw);self._qt.setRange(0,10000);self._qt.valueChanged.connect(lambda v:self._opts.get('command') and self._opts['command']('moveto',v/10000));self.set(0,1)
    def _apply(self):
        super()._apply();self._qt.setOrientation(C.Qt.Orientation.Horizontal if self._opts.get('orient')=='horizontal' else C.Qt.Orientation.Vertical);style=_styles.get(self._opts.get('style'),{});bg=style.get('troughcolor','#181614');face=style.get('background','#514333');self._qt.setStyleSheet(f'QScrollBar:vertical {{background:{bg};width:12px;}} QScrollBar::handle:vertical {{background:{face};min-height:20px;border-radius:4px;}} QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0;}} QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {{background:{bg};}} QScrollBar:horizontal {{background:{bg};height:10px;}} QScrollBar::handle:horizontal {{background:{face};min-width:20px;border-radius:4px;}} QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {{width:0;}} QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal {{background:{bg};}}')
    def _request(self):return (12,80) if self._opts.get('orient','vertical')=='vertical' else (80,12)
    def set(self,first,last):
        self._qt.blockSignals(True);span=max(0,min(1,float(last)-float(first)));self._qt.setRange(0,round((1-span)*10000));self._qt.setValue(round(float(first)*10000));self._qt.setPageStep(max(1,round((float(last)-float(first))*10000)));self._qt.setEnabled(float(last)-float(first)<.999);self._qt.blockSignals(False)

class Style:
    def __init__(self,master=None):pass
    def configure(self,name,**kw):
        _styles.setdefault(name,{}).update(kw)
        for widget in tuple(_registry.values()):
            if widget.cget('style')==name:widget._apply()
    def map(self,name,**kw):
        for option,values in kw.items():
            for state,value in values:
                if state=='selected':self.configure(name,**{'selection'+option:value})
    def theme_use(self,name=None):return 'Fusion'
    def element_names(self):return ()
    def layout(self,*args):return []
    def element_create(self,*args,**kw):pass

class Treeview(Widget):
    native=W.QTreeWidget
    def __init__(self,master=None,**kw):
        self._items={};self._tags={};self._cols=tuple(kw.get('columns',()));self._colopts={};self._headings={};super().__init__(master,**kw)
        self._qt.setRootIsDecorated(kw.get('show','tree')!='headings');self._qt.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection if kw.get('selectmode')=='extended' else W.QAbstractItemView.SelectionMode.SingleSelection)
        self._qt.itemSelectionChanged.connect(lambda:self.event_generate('<<TreeviewSelect>>'));self._qt.itemExpanded.connect(lambda item:self.event_generate('<<TreeviewOpen>>'))
        self._qt.header().sectionClicked.connect(self._heading_click);self._qt.verticalScrollBar().valueChanged.connect(self._scroll);self._qt.verticalScrollBar().rangeChanged.connect(self._scroll);self._qt.horizontalScrollBar().valueChanged.connect(self._scroll_x);self._qt.horizontalScrollBar().rangeChanged.connect(self._scroll_x)
    def _apply(self):
        super()._apply();o=self._opts;s=_styles.get(o.get('style'),{});bg=s.get('background',o['bg']);fg=s.get('foreground',o['fg']);self._qt.setColumnCount(len(self._cols)+(0 if o.get('show')=='headings' else 1));self._qt.setFont(qfont(s.get('font',o['font'])));self._qt.setStyleSheet(f'QTreeWidget {{background:{bg};color:{fg};border:none;outline:none;}} QTreeWidget::item {{height:{s.get("rowheight",28)}px;}} QTreeWidget::item:selected {{background:{s.get('selectionbackground','#473a27')};color:{s.get('selectionforeground','#fff1c7')};}} QHeaderView::section {{background:{s.get("fieldbackground",bg)};color:{fg};border:none;padding:6px;}}');self._qt.setHorizontalScrollBarPolicy(C.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._qt.setVerticalScrollBarPolicy(C.Qt.ScrollBarPolicy.ScrollBarAlwaysOff if o.get('yscrollcommand') else C.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        heading=_styles.get(o.get('style','')+'.Heading',{});self._qt.headerItem().setFont(0,qfont(heading.get('font',s.get('font',o['font']))))
    def _col(self,c):
        if c=='#0':return 0
        if isinstance(c,int):return c
        return self._cols.index(c)+(0 if self._opts.get('show')=='headings' else 1)
    def heading(self,col,**kw):
        i=self._col(col);self._headings[i]=kw;item=self._qt.headerItem();item.setText(i,kw.get('text',str(col)));item.setFont(i,qfont(_styles.get(self._opts.get('style','')+'.Heading',{}).get('font',self._opts['font'])));return kw
    def column(self,col,**kw):
        i=self._col(col);self._colopts[i]=kw
        if kw.get('width'):self._qt.setColumnWidth(i,int(kw['width']))
        mode=W.QHeaderView.ResizeMode.Stretch if kw.get('stretch',False) else W.QHeaderView.ResizeMode.Interactive;self._qt.header().setSectionResizeMode(i,mode)
        return kw
    def _heading_click(self,i):
        cb=self._headings.get(i,{}).get('command')
        if cb:cb()
    def insert(self,parent,index,iid=None,**kw):
        iid=str(iid or next(_ids));item=W.QTreeWidgetItem();self._items[iid]=item;item.setData(0,C.Qt.ItemDataRole.UserRole,iid)
        if parent:self._items[str(parent)].addChild(item)
        else:self._qt.addTopLevelItem(item)
        self.item(iid,**kw);return iid
    def item(self,iid,option=None,**kw):
        item=self._items[str(iid)];base=0 if self._opts.get('show')=='headings' else 1
        if 'text' in kw:item.setText(0,str(kw['text']))
        if 'values' in kw:
            for i,v in enumerate(kw['values']):item.setText(i+base,str(v))
        if 'image' in kw and kw['image']:item.setIcon(0,G.QIcon(kw['image'].qt))
        if 'open' in kw:item.setExpanded(bool(kw['open']))
        if 'tags' in kw:item.setData(0,C.Qt.ItemDataRole.UserRole+1,tuple(kw['tags']) if not isinstance(kw['tags'],str) else (kw['tags'],));self._apply_tags(item)
        d={'text':item.text(0),'values':tuple(item.text(i+base) for i in range(len(self._cols))),'open':item.isExpanded(),'tags':item.data(0,C.Qt.ItemDataRole.UserRole+1) or ()}
        return d.get(option) if option else d
    def _apply_tags(self,item):
        for tag in item.data(0,C.Qt.ItemDataRole.UserRole+1) or ():
            o=self._tags.get(tag,{})
            for i in range(self._qt.columnCount()):
                if 'background' in o:item.setBackground(i,color(o['background']))
                if 'foreground' in o:item.setForeground(i,color(o['foreground']))
                if 'font' in o:item.setFont(i,qfont(o['font']))
    def tag_configure(self,tag,**kw):
        self._tags[tag]=kw
        for item in self._items.values():self._apply_tags(item)
    tag_config=tag_configure
    def get_children(self,parent=''):
        item=self._items.get(str(parent));items=[item.child(i) for i in range(item.childCount())] if item else [self._qt.topLevelItem(i) for i in range(self._qt.topLevelItemCount())];return tuple(i.data(0,C.Qt.ItemDataRole.UserRole) for i in items)
    def delete(self,*ids):
        for iid in ids:
            if isinstance(iid,(tuple,list)):self.delete(*iid);continue
            item=self._items.get(str(iid))
            if item:
                self.delete(*self.get_children(str(iid)));self._items.pop(str(iid),None);p=item.parent();p.takeChild(p.indexOfChild(item)) if p else self._qt.takeTopLevelItem(self._qt.indexOfTopLevelItem(item))
    def exists(self,iid):return str(iid) in self._items
    def selection(self):return tuple(i.data(0,C.Qt.ItemDataRole.UserRole) for i in self._qt.selectedItems())
    def selection_set(self,*ids):
        if len(ids)==1 and isinstance(ids[0],(tuple,list)):ids=ids[0]
        self._qt.clearSelection()
        for i in ids:
            if str(i) in self._items:self._items[str(i)].setSelected(True)
    def selection_remove(self,*ids):
        for i in ids:
            if str(i) in self._items:self._items[str(i)].setSelected(False)
    def focus(self,iid=None):
        if iid is not None:self._qt.setCurrentItem(self._items[str(iid)])
        item=self._qt.currentItem();return item.data(0,C.Qt.ItemDataRole.UserRole) if item else ''
    def identify_row(self,y):item=self._qt.itemAt(4,int(y)-self._qt.header().height());return item.data(0,C.Qt.ItemDataRole.UserRole) if item else ''
    def identify_column(self,x):return '#'+str(self._qt.header().logicalIndexAt(int(x))+1)
    def see(self,iid):self._qt.scrollToItem(self._items[str(iid)])
    def set(self,iid,col=None,value=None):
        if value is None:return self.item(iid,'values')[self._cols.index(col)] if col else dict(zip(self._cols,self.item(iid,'values')))
        self._items[str(iid)].setText(self._col(col),str(value))
    def _request(self):return 300,int(self._opts.get('height',10))*28+32
    def _scroll(self,*args):
        cb=self._opts.get('yscrollcommand');b=self._qt.verticalScrollBar()
        if cb:cb(b.value()/max(1,b.maximum()+b.pageStep()),(b.value()+b.pageStep())/max(1,b.maximum()+b.pageStep()))
    def yview(self,*args):
        b=self._qt.verticalScrollBar()
        if args:b.setValue(round(float(args[1])*(b.maximum()+b.pageStep())) if args[0]=='moveto' else b.value()+int(args[1])*b.singleStep())
    def _scroll_x(self,*args):
        b=self._qt.horizontalScrollBar();cb=self._opts.get('xscrollcommand')
        if cb:cb(b.value()/max(1,b.maximum()+b.pageStep()),(b.value()+b.pageStep())/max(1,b.maximum()+b.pageStep()))
    def xview(self,*args):
        b=self._qt.horizontalScrollBar()
        if args:b.setValue(round(float(args[1])*(b.maximum()+b.pageStep())) if args[0]=='moveto' else b.value()+int(args[1])*b.singleStep())
    def move(self,iid,parent,index):
        item=self._items[str(iid)];old=item.parent();old.takeChild(old.indexOfChild(item)) if old else self._qt.takeTopLevelItem(self._qt.indexOfTopLevelItem(item));p=self._items.get(str(parent));p.insertChild(int(index),item) if p else self._qt.insertTopLevelItem(int(index),item)

class Progressbar(Widget):
    native=W.QProgressBar
    def __init__(self,master=None,**kw):super().__init__(master,**kw);self._qt.setTextVisible(False)
    def _apply(self):super()._apply();style=_styles.get(self._opts.get('style'),{});self._qt.setStyleSheet(f'QProgressBar {{background:{style.get("troughcolor","#181614")};border:none;}} QProgressBar::chunk {{background:{style.get("background","#e3c36e")};}}');self._qt.setRange(0,int(self._opts.get('maximum',100)));self._qt.setValue(int(self._opts.get('value',0)))
    def start(self,interval=50):self._qt.setRange(0,0)
    def stop(self):self._qt.setRange(0,int(self._opts.get('maximum',100)))
    def _request(self):return int(self._opts.get('length',100)),14

controls=SimpleNamespace(Combobox=ComboBox,Style=Style,Scrollbar=Scrollbar,Treeview=Treeview,Progressbar=Progressbar)

class Text(Widget):
    native=W.QTextEdit
    def __init__(self,master=None,**kw):
        self._marks={};self._tags={};self._ranges=[];super().__init__(master,**kw);self._qt.setAcceptRichText(False);self._qt.setHorizontalScrollBarPolicy(C.Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self._qt.verticalScrollBar().valueChanged.connect(self._scroll);self._qt.verticalScrollBar().rangeChanged.connect(self._scroll);self._qt.horizontalScrollBar().valueChanged.connect(self._scroll_x);self._qt.horizontalScrollBar().rangeChanged.connect(self._scroll_x)
    def _apply(self):
        super()._apply();self._qt.setReadOnly(self._opts.get('state')=='disabled');self._qt.setEnabled(True);o=self._opts;bd=max(int(o.get('bd',0)),int(o.get('highlightthickness',0)));self._qt.setVerticalScrollBarPolicy(C.Qt.ScrollBarPolicy.ScrollBarAlwaysOff if o.get('yscrollcommand') else C.Qt.ScrollBarPolicy.ScrollBarAsNeeded);self._qt.setStyleSheet(f'QTextEdit {{background:{o["bg"]};color:{o["fg"]};border:{bd}px solid {o.get("highlightbackground",o["bg"])};padding:6px;}}');self._qt.setLineWrapMode(W.QTextEdit.LineWrapMode.NoWrap if o.get('wrap')=='none' else W.QTextEdit.LineWrapMode.WidgetWidth)
    def _request(self):fm=G.QFontMetrics(self._qt.font());return int(self._opts.get('width',40))*fm.horizontalAdvance('0')+12,int(self._opts.get('height',10))*fm.height()+12
    def _pos(self,index):
        text=self._qt.toPlainText();s=str(index)
        if s in ('end','end-1c'):return len(text)
        if s=='insert':return self._qt.textCursor().position()
        if s.startswith('@'):return self._qt.cursorForPosition(C.QPoint(0,0)).position()
        match=re.match(r'(.+?)([+-])(\d+)c$',s)
        if match:return max(0,self._pos(match[1])+(1 if match[2]=='+' else -1)*int(match[3]))
        if s in self._marks:return self._marks[s].position()
        if '.' in s:
            line,col=s.split('.',1);lines=text.splitlines(True);return min(len(text),sum(len(l) for l in lines[:max(0,int(line)-1)])+(len(lines[int(line)-1]) if col=='end' and int(line)<=len(lines) else int(col)))
        return int(s)
    def _cursor(self,index):c=G.QTextCursor(self._qt.document());c.setPosition(min(self._pos(index),self._qt.document().characterCount()-1));return c
    def insert(self,index,text,tags=()):
        c=self._cursor(index);fmt=G.QTextCharFormat();fmt.setFont(self._qt.font());fmt.setForeground(color(self._opts['fg']))
        for tag in (tags,) if isinstance(tags,str) else tags:
            o=self._tags.get(tag,{})
            if 'font' in o:fmt.setFont(qfont(o['font']))
            if 'foreground' in o:fmt.setForeground(color(o['foreground']))
            if o.get('underline'):fmt.setFontUnderline(True)
        c.insertText(str(text),fmt)
    def get(self,start='1.0',end='end'):return self._qt.toPlainText()[self._pos(start):self._pos(end)]
    def delete(self,start,end=None):c=self._cursor(start);c.setPosition(self._pos(end) if end else self._pos(start)+1,G.QTextCursor.MoveMode.KeepAnchor);c.removeSelectedText()
    def index(self,index):p=self._pos(index);t=self._qt.toPlainText()[:p];return f'{t.count(chr(10))+1}.{len(t.rsplit(chr(10),1)[-1])}'
    def tag_configure(self,tag,**kw):self._tags[tag]=kw
    tag_config=tag_configure
    def tag_add(self,tag,start,end):
        c=self._cursor(start);c.setPosition(self._pos(end),G.QTextCursor.MoveMode.KeepAnchor);f=G.QTextCharFormat();o=self._tags.get(tag,{})
        if 'font' in o:f.setFont(qfont(o['font']))
        if 'foreground' in o:f.setForeground(color(o['foreground']))
        c.mergeCharFormat(f)
    def tag_delete(self,tag):self._tags.pop(tag,None)
    def tag_bind(self,*args):pass
    def mark_set(self,name,index):self._marks[name]=self._cursor(index)
    def mark_gravity(self,name,gravity):
        if name in self._marks:self._marks[name].setKeepPositionOnInsert(gravity=='left')
    def mark_unset(self,name):self._marks.pop(name,None)
    def image_create(self,index,image,**kw):
        name='img'+str(next(_ids));self._qt.document().addResource(G.QTextDocument.ResourceType.ImageResource,C.QUrl(name),image.qt.toImage());fmt=G.QTextImageFormat();fmt.setName(name);fmt.setWidth(image.width());fmt.setHeight(image.height());self._cursor(index).insertImage(fmt);return name
    def see(self,index):self._qt.setTextCursor(self._cursor(index));self._qt.ensureCursorVisible()
    def edit_reset(self):self._qt.document().clearUndoRedoStacks()
    def edit_undo(self):self._qt.undo()
    def edit_redo(self):self._qt.redo()
    def _scroll_x(self,*args):
        b=self._qt.horizontalScrollBar();cb=self._opts.get('xscrollcommand')
        if cb:cb(b.value()/max(1,b.maximum()+b.pageStep()),(b.value()+b.pageStep())/max(1,b.maximum()+b.pageStep()))
    def xview(self,*args):
        b=self._qt.horizontalScrollBar()
        if args:b.setValue(round(float(args[1])*(b.maximum()+b.pageStep())) if args[0]=='moveto' else b.value()+int(args[1])*b.singleStep())
    def yview(self,*args):
        b=self._qt.verticalScrollBar()
        if args:b.setValue(round(float(args[1])*(b.maximum()+b.pageStep())) if args[0]=='moveto' else b.value()+int(args[1])*b.singleStep())
    def yview_moveto(self,f):self.yview('moveto',f)
    def yview_scroll(self,n,what):self.yview('scroll',n,what)
    def _scroll(self,*args):
        b=self._qt.verticalScrollBar();cb=self._opts.get('yscrollcommand')
        if cb:cb(b.value()/max(1,b.maximum()+b.pageStep()),(b.value()+b.pageStep())/max(1,b.maximum()+b.pageStep()))

class filedialog:
    @staticmethod
    def askdirectory(**kw):return W.QFileDialog.getExistingDirectory(None,kw.get('title','Choose folder'),kw.get('initialdir',''))
    @staticmethod
    def askopenfilename(**kw):return W.QFileDialog.getOpenFileName(None,kw.get('title','Choose file'),kw.get('initialdir',''),_filefilters(kw))[0]
    @staticmethod
    def asksaveasfilename(**kw):return W.QFileDialog.getSaveFileName(None,kw.get('title','Save file'),str(Path(kw.get('initialdir',''))/kw.get('initialfile','')),_filefilters(kw))[0]
    @staticmethod
    def askopenfilenames(**kw):return W.QFileDialog.getOpenFileNames(None,kw.get('title','Choose files'),kw.get('initialdir',''),_filefilters(kw))[0]
def _filefilters(kw):return ';;'.join(f'{name} ({pattern})' for name,pattern in kw.get('filetypes',[]))
class messagebox:
    @staticmethod
    def showinfo(title,message,**kw):return W.QMessageBox.information(None,title,str(message))
    @staticmethod
    def showerror(title,message,**kw):return W.QMessageBox.critical(None,title,str(message))
    @staticmethod
    def showwarning(title,message,**kw):return W.QMessageBox.warning(None,title,str(message))
    @staticmethod
    def askyesno(title,message,**kw):return W.QMessageBox.question(None,title,str(message))==W.QMessageBox.StandardButton.Yes
    askokcancel=askyesno
    @staticmethod
    def askyesnocancel(title,message,**kw):
        result=W.QMessageBox.question(None,title,str(message),W.QMessageBox.StandardButton.Yes|W.QMessageBox.StandardButton.No|W.QMessageBox.StandardButton.Cancel)
        return None if result==W.QMessageBox.StandardButton.Cancel else result==W.QMessageBox.StandardButton.Yes
class simpledialog:
    @staticmethod
    def askstring(title,prompt,**kw):value,ok=W.QInputDialog.getText(None,title,prompt,text=kw.get('initialvalue',''));return value if ok else None

class NativeHost(Frame):
    """Layout host for an existing Qt widget; no image bridge or event proxy."""
    def __init__(self,master,widget,**kw):
        super().__init__(master,**kw);self.widget=widget;widget.setParent(self._qt);widget.show()
    def _layout(self):
        super()._layout()
        if hasattr(self,'widget') and not self._dead:self.widget.setGeometry(self._qt.rect())
    def _request(self):return 100,100
