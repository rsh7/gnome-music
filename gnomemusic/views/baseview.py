# Copyright (c) 2016 The GNOME Music Developers
#
# GNOME Music is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# GNOME Music is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with GNOME Music; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# The GNOME Music authors hereby grant permission for non-GPL compatible
# GStreamer plugins to be used and distributed together with GStreamer
# and GNOME Music.  This permission is above and beyond the permissions
# granted by the GPL license by which GNOME Music is covered.  If you
# modify this code, you may extend this exception to your version of the
# code, but you are not obligated to do so.  If you do not wish to do so,
# delete this exception statement from your version.

from gettext import gettext as _, ngettext
from gi.repository import Gd, Gdk, GdkPixbuf, GObject, Gtk

from gnomemusic import log
from gnomemusic.albumartcache import AlbumArtCache, DefaultIcon, ArtSize
from gnomemusic.grilo import grilo
from gnomemusic.widgets.starhandlerwidget import StarHandlerWidget
import gnomemusic.utils as utils


class BaseView(Gtk.Stack):
    nowPlayingIconName = 'media-playback-start-symbolic'
    errorIconName = 'dialog-error-symbolic'

    selection_mode = GObject.Property(type=bool, default=False)

    def __repr__(self):
        return '<BaseView>'

    @log
    def __init__(self, name, title, window, view_type, use_sidebar=False, sidebar=None):
        Gtk.Stack.__init__(self,
                           transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._grid = Gtk.Grid(orientation=Gtk.Orientation.HORIZONTAL)
        self._offset = 0
        self._adjustmentValueId = 0
        self._adjustmentChangedId = 0
        self._scrollbarVisibleId = 0
        self.old_vsbl_range = None
        self.model = Gtk.ListStore(
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
            GdkPixbuf.Pixbuf,
            GObject.TYPE_OBJECT,
            GObject.TYPE_BOOLEAN,
            GObject.TYPE_INT,
            GObject.TYPE_STRING,
            GObject.TYPE_INT,
            GObject.TYPE_BOOLEAN,
            GObject.TYPE_INT
        )

        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Setup the main view
        self._setup_view(view_type)

        if use_sidebar:
            self.stack = Gtk.Stack(
                transition_type=Gtk.StackTransitionType.SLIDE_RIGHT,
            )
            dummy = Gtk.Frame(visible=False)
            self.stack.add_named(dummy, 'dummy')
            if sidebar:
                self.stack.add_named(sidebar, 'sidebar')
            else:
                self.stack.add_named(self._box, 'sidebar')
            self.stack.set_visible_child_name('dummy')
            self._grid.add(self.stack)
        if not use_sidebar or sidebar:
            self._grid.add(self._box)

        self.star_handler = StarHandlerWidget(self, 9)
        self._cursor = None
        self.window = window
        self.header_bar = window.toolbar
        self.selection_toolbar = window.selection_toolbar
        self.header_bar._select_button.connect(
            'toggled', self._on_header_bar_toggled)
        self.header_bar._cancel_button.connect(
            'clicked', self._on_cancel_button_clicked)

        self.name = name
        self.title = title
        self.add(self._grid)

        self.show_all()
        self.view.hide()
        self._items = []

        scale = self.get_scale_factor()
        self.cache = AlbumArtCache(scale)
        self._loading_icon_surface = DefaultIcon(scale).get(
            DefaultIcon.Type.loading,
            ArtSize.medium)


        self._init = False
        grilo.connect('ready', self._on_grilo_ready)
        self.selection_socket = None
        self.header_bar.connect('selection-mode-changed',
                                self._on_selection_mode_changed)

        self._discovering_urls = {}
        grilo.connect('changes-pending', self._on_changes_pending)

    @log
    def _on_changes_pending(self, data=None):
        pass

    @log
    def _setup_view(self, view_type):
        self.view = Gd.MainView(shadow_type=Gtk.ShadowType.NONE)
        self.view.set_view_type(view_type)

        self.view.click_handler = self.view.connect('item-activated', self._on_item_activated)
        self.view.connect('selection-mode-request', self._on_selection_mode_request)

        self.view.bind_property('selection-mode', self, 'selection_mode',
                                GObject.BindingFlags.BIDIRECTIONAL)

        self.view.connect('view-selection-changed', self._on_view_selection_changed)

        self._box.pack_start(self.view, True, True, 0)

    @log
    def _on_header_bar_toggled(self, button):
        self.selection_mode = button.get_active()

        if self.selection_mode:
            self.header_bar.set_selection_mode(True)
            self.player.actionbar.set_visible(False)
            self.selection_toolbar.actionbar.set_visible(True)
            self.selection_toolbar._add_to_playlist_button.set_sensitive(False)
            self.selection_toolbar._remove_from_playlist_button.set_sensitive(False)
        else:
            self.header_bar.set_selection_mode(False)
            self.player.actionbar.set_visible(self.player.currentTrack is not None)
            self.selection_toolbar.actionbar.set_visible(False)
            self.unselect_all()

    @log
    def _on_cancel_button_clicked(self, button):
        self.view.set_selection_mode(False)
        self.header_bar.set_selection_mode(False)

    @log
    def _on_grilo_ready(self, data=None):
        # FIXME: with async changes in Window this seems never to be
        # called anymore. Fix it proper or remove.
        if (self.header_bar.get_stack().get_visible_child() == self and not self._init):
            self._populate()
        self.header_bar.get_stack().connect('notify::visible-child',
                                            self._on_headerbar_visible)

    @log
    def _on_headerbar_visible(self, widget, param):
        if self == widget.get_visible_child() and not self._init:
            self._populate()

    @log
    def _on_view_selection_changed(self, widget):
        if not self.selection_mode:
            return

        items = self.view.get_selection()
        self.update_header_from_selection(len(items))

    @log
    def update_header_from_selection(self, n_items):
        self.selection_toolbar._add_to_playlist_button.\
            set_sensitive(n_items > 0)
        self.selection_toolbar._remove_from_playlist_button.\
            set_sensitive(n_items > 0)
        if n_items > 0:
            self.header_bar._selection_menu_label.set_text(
                ngettext("Selected %d item", "Selected %d items", n_items) % n_items)
        else:
            self.header_bar._selection_menu_label.set_text(_("Click on items to select them"))

    @log
    def _populate(self, data=None):
        self._init = True
        self.populate()

    @log
    def _on_selection_mode_changed(self, widget, data=None):
        pass

    @log
    def populate(self):
        pass

    @log
    def _add_item(self, source, param, item, remaining=0, data=None):
        if not item:
            if remaining == 0:
                self.view.set_model(self.model)
                self.window.pop_loading_notification()
                self.view.show()
            return

        self._offset += 1
        artist = utils.get_artist_name(item)
        title = utils.get_media_title(item)

        _iter = self.model.append(None)

        loading_icon = Gdk.pixbuf_get_from_surface(
            self._loadin_icon_surface, 0, 0,
            self._loading_icon_surface.get_width(),
            self._loading_icon_surface.get_height())

        self.model[_iter][0, 1, 2, 3, 4, 5, 7, 9] = [
            str(item.get_id()),
            '',
            title,
            artist,
            loading_icon,
            item,
            0,
            False
        ]
        self.cache.lookup(item, self._iconWidth, self._iconHeight,
                          self._on_lookup_ready, _iter)

    @log
    def _on_lookup_ready(self, surface, _iter):
        if surface:
            pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0,
                                                 surface.get_width(),
                                                 surface.get_height())

            self.model[_iter][4] = pixbuf

    @log
    def _add_list_renderers(self):
        pass

    @log
    def _on_item_activated(self, widget, id, path):
        pass

    @log
    def _on_selection_mode_request(self, *args):
        self.header_bar._select_button.clicked()

    @log
    def get_selected_tracks(self, callback):
        callback([])

    @log
    def _set_selection(self, value, parent=None):
        count = 0
        _iter = self.model.iter_children(parent)
        while _iter is not None:
            if self.model.iter_has_child(_iter):
                count += self._set_selection(value, _iter)
            if self.model[_iter][5]:
                self.model[_iter][6] = value
                count += 1
            _iter = self.model.iter_next(_iter)
        return count

    @log
    def select_all(self):
        """Select all the available tracks."""
        count = self._set_selection(True)

        if count > 0:
            self.selection_toolbar._add_to_playlist_button.set_sensitive(True)
            self.selection_toolbar._remove_from_playlist_button.set_sensitive(True)

        self.update_header_from_selection(count)
        self.view.queue_draw()

    @log
    def unselect_all(self):
        """Unselects all the selected tracks."""
        self._set_selection(False)
        self.selection_toolbar._add_to_playlist_button.set_sensitive(False)
        self.selection_toolbar._remove_from_playlist_button.set_sensitive(False)
        self.header_bar._selection_menu_label.set_text(_("Click on items to select them"))
        self.queue_draw()
