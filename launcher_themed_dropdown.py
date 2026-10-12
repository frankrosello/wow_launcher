"""Game-themed dropdowns drawn inside the launcher, without native popdowns."""
import tkinter as tk


class ThemedOptionMenu(tk.Frame):
    def __init__(self, master, variable, value, *values, command=None):
        self.variable = variable
        self._popup_root = master.winfo_toplevel()
        self._colors = {'bg':'#20313d','fg':'#e8dfcb','activebackground':'#2d4655',
                        'activeforeground':'#fff7eb','bordercolor':'#435360','focusbordercolor':'#e3c36e'}
        self._popup = None
        self._outside_binding = None
        self._disabled = False
        super().__init__(master, bg=self._colors['bg'], highlightthickness=1,
                         highlightbackground=self._colors['bordercolor'], bd=0)
        self._menu = tk.Menu(self, tearoff=False)
        for item in (value,*values):
            self._menu.add_command(label=item,command=tk._setit(variable,item,command))
        self.label = tk.Label(self,textvariable=variable,anchor='w',bg=self._colors['bg'],
                              fg=self._colors['fg'],padx=9,pady=5,bd=0,takefocus=True)
        self.label.pack(side='left',fill='both',expand=True)
        self.arrow = tk.Canvas(self,width=22,height=24,bd=0,highlightthickness=0,bg=self._colors['bg'])
        self.arrow.pack(side='right',padx=(0,4))
        for widget in (self,self.label,self.arrow):
            widget.bind('<Button-1>',self._toggle)
            widget.bind('<Return>',self._toggle)
            widget.bind('<space>',self._toggle)
            widget.bind('<Down>',self._toggle)
        self.bind('<Destroy>',self._destroyed,add='+')
        self._paint()

    def __getitem__(self,key):
        return self._menu if key=='menu' else super().__getitem__(key)

    def cget(self,key):
        if key in ('font','width','anchor') and hasattr(self,'label'):
            return self.label.cget(key)
        if key in self._colors:
            return self._colors[key]
        return super().cget(key)

    def configure(self,cnf=None,**options):
        if cnf: options.update(cnf)
        if not hasattr(self,'label'):
            return super().configure(**options)
        if not options: return super().configure()
        for key,alias in (('background','bg'),('foreground','fg')):
            if key in options: options[alias]=options.pop(key)
        for key in self._colors:
            if key in options: self._colors[key]=options.pop(key)
        for key in ('font','width','anchor'):
            if key in options: self.label.configure(**{key:options.pop(key)})
        if 'state' in options:
            self._disabled=options.pop('state')=='disabled'
        for key in ('padx','pady','relief','bd','borderwidth','highlightthickness','highlightbackground','highlightcolor'):
            options.pop(key,None)
        result=super().configure(**options) if options else None
        self._paint()
        return result
    config=configure

    def _paint(self):
        c=self._colors
        super().configure(bg=c['bg'],highlightbackground=c['bordercolor'])
        self.label.configure(bg=c['bg'],fg=c['fg'])
        self.arrow.configure(bg=c['bg'])
        self.arrow.delete('all')
        self.arrow.create_polygon(6,10,16,10,11,15,fill=c['fg'],outline='')

    def _toggle(self,event=None):
        if self._popup is not None: self._close()
        elif not self._disabled: self._open()
        return 'break'

    def _open(self):
        end=self._menu.index('end')
        if end is None: return
        self.update_idletasks()
        root=self.winfo_toplevel()
        c=self._colors
        popup=self._popup=tk.Frame(root,bg=c['bg'],bd=0,highlightthickness=1,
                                    highlightbackground=c['bordercolor'])
        count=end+1
        self._list=tk.Listbox(popup,height=min(8,count),exportselection=False,
            bg=c['bg'],fg=c['fg'],selectbackground=c['activebackground'],
            selectforeground=c['activeforeground'],font=self.cget('font'),
            activestyle='none',bd=0,highlightthickness=0)
        self._list.pack(side='left',fill='both',expand=True)
        for i in range(count):
            self._list.insert('end',self._menu.entrycget(i,'label'))
            if self._menu.entrycget(i,'label') == self.variable.get():
                self._list.selection_set(i);self._list.activate(i)
            if self._menu.entrycget(i,'state')=='disabled':
                self._list.itemconfigure(i,fg=self._menu.entrycget(i,'foreground') or '#827b70')
        if count>8:
            bar=tk.Scrollbar(popup,command=self._list.yview,bg=c['bg'],troughcolor=c['bg'],
                             activebackground=c['activebackground'],bd=0,highlightthickness=0,width=12)
            bar.pack(side='right',fill='y');self._list.configure(yscrollcommand=bar.set)
        popup.update_idletasks()
        width=max(self.winfo_width(),popup.winfo_reqwidth())
        width=min(width,root.winfo_width()-28)
        height=min(popup.winfo_reqheight(),root.winfo_height()-40)
        x=max(14,min(self.winfo_rootx()-root.winfo_rootx(),root.winfo_width()-width-14))
        y=self.winfo_rooty()-root.winfo_rooty()+self.winfo_height()
        if y+height>root.winfo_height()-14:
            y=max(14,self.winfo_rooty()-root.winfo_rooty()-height)
        popup.place(x=x,y=y,width=width,height=height)
        popup.lift();self._list.focus_set()
        self._list.bind('<ButtonRelease-1>',lambda e:self._choose(self._list.nearest(e.y)))
        self._list.bind('<Return>',lambda e:self._choose_selected())
        self._list.bind('<Escape>',lambda e:self._close())
        def outside(event):
            if self._popup is None: return
            name=str(event.widget)
            if not name.startswith(str(self._popup)) and not name.startswith(str(self)):
                self._close()
        self._outside_binding=root.bind('<ButtonPress-1>',outside,add='+')

    def _choose_selected(self):
        selected=self._list.curselection()
        if selected: self._choose(selected[0])
        return 'break'

    def _choose(self,index):
        if self._menu.entrycget(index,'state')=='disabled': return 'break'
        self._close()
        self._menu.invoke(index)
        return 'break'

    def _close(self):
        popup,self._popup=self._popup,None
        if popup is not None: popup.destroy()
        if self._outside_binding:
            self._popup_root.unbind('<ButtonPress-1>',self._outside_binding)
            self._outside_binding=None
        if self.winfo_exists(): self.label.focus_set()
        return 'break'

    def _destroyed(self,event):
        if event.widget is self: self._close()
