#!/usr/bin/python
# -*- encoding: utf-8; py-indent-offset: 4 -*-
# +------------------------------------------------------------------+
# |             ____ _               _        __  __ _  __           |
# |            / ___| |__   ___  ___| | __   |  \/  | |/ /           |
# |           | |   | '_ \ / _ \/ __| |/ /   | |\/| | ' /            |
# |           | |___| | | |  __/ (__|   <    | |  | | . \            |
# |            \____|_| |_|\___|\___|_|\_\___|_|  |_|_|\_\           |
# |                                                                  |
# | Copyright Mathias Kettner 2013             mk@mathias-kettner.de |
# +------------------------------------------------------------------+
#
# This file is part of Check_MK.
# The official homepage is at http://mathias-kettner.de/check_mk.
#
# check_mk is free software;  you can redistribute it and/or modify it
# under the  terms of the  GNU General Public License  as published by
# the Free Software Foundation in version 2.  check_mk is  distributed
# in the hope that it will be useful, but WITHOUT ANY WARRANTY;  with-
# out even the implied warranty of  MERCHANTABILITY  or  FITNESS FOR A
# PARTICULAR PURPOSE. See the  GNU General Public License for more de-
# ails.  You should have  received  a copy of the  GNU  General Public
# License along with GNU Make; see the file  COPYING.  If  not,  write
# to the Free Software Foundation, Inc., 51 Franklin St,  Fifth Floor,
# Boston, MA 02110-1301 USA.

import config, defaults, visuals, pprint, time
from valuespec import *
from lib import *
import wato

# Python 2.3 does not have 'set' in normal namespace.
# But it can be imported from 'sets'
try:
    set()
except NameError:
    from sets import Set as set

loaded_with_language = False
builtin_dashboards = {}
dashlets = {}

# Declare constants to be used in the definitions of the dashboards
GROW = 0
MAX = -1

# These settings might go into the config module, sometime in future,
# in order to allow the user to customize this.

header_height   = 60             # Distance from top of the screen to the lower border of the heading
screen_margin   = 5              # Distance from the left border of the main-frame to the dashboard area
dashlet_padding = 21, 5, 5, 0 # Margin (N, E, S, W) between outer border of dashlet and its content
corner_overlap  = 22
title_height    = 0             # Height of dashlet title-box
raster          = 10, 10        # Raster the dashlet choords are measured in

# Load plugins in web/plugins/dashboard and declare permissions,
# note: these operations produce language-specific results and
# thus must be reinitialized everytime a language-change has
# been detected.
def load_plugins():
    global loaded_with_language, dashboards
    if loaded_with_language == current_language:
        return

    # Load plugins for dashboards. Currently these files
    # just may add custom dashboards by adding to builtin_dashboards.
    load_web_plugins("dashboard", globals())

    # be compatible to old definitions, where even internal dashlets were
    # referenced by url, e.g. dashboard['url'] = 'hoststats.py'
    # FIXME: can be removed one day. Mark as incompatible change or similar.
    for name, dashboard in builtin_dashboards.items():
        for dashlet in dashboard['dashlets']:
            dashlet.setdefault('parameters', {})
            if dashlet.get('url', '').startswith('dashlet_') and dashlet['url'].endswith('.py'):
                dashlet['type'] = dashlet['url'][8:-3]
                del dashlet['url']
            elif dashlet.get('url', '') != '':
                dashlet['type'] = 'url'
            elif dashlet.get('view', '') != '':
                dashlet['type'] = 'view'
                dashlet['parameters']['view_name'] = dashlet['view']
                del dashlet['view']

        # add the modification time to make reload of dashboards work
        dashboard['mtime'] = int(time.time())

        dashboard.setdefault('show_title', True)
        if dashboard['title'] == None:
            dashboard['title'] = _('No title')
            dashboard['show_title'] = False

    # This must be set after plugin loading to make broken plugins raise
    # exceptions all the time and not only the first time (when the plugins
    # are loaded).
    loaded_with_language = current_language

    # Clear this structure to prevent users accessing dashboard structures created
    # by other users, make them see these dashboards
    dashboards = {}

    # Declare permissions for all dashboards
    config.declare_permission_section("dashboard", _("Dashboards"), do_sort = True)
    for name, board in builtin_dashboards.items():
        config.declare_permission("dashboard.%s" % name,
                board["title"],
                board["description"],
                config.builtin_role_ids)

    # Make sure that custom views also have permissions
    config.declare_dynamic_permissions(lambda: visuals.declare_custom_permissions('dashboards'))

def load_dashboards():
    global dashboards, available_dashboards
    dashboards = visuals.load('dashboards', builtin_dashboards)
    available_dashboards = visuals.available('dashboards', dashboards)

def save_dashboards(us):
    visuals.save('dashboards', dashboards)

def permitted_dashboards():
    return available_dashboards

# HTML page handler for generating the (a) dashboard. The name
# of the dashboard to render is given in the HTML variable 'name'.
# This defaults to "main".
def page_dashboard():
    load_dashboards()

    name = html.var("name", "main")
    if name not in available_dashboards:
        raise MKGeneralException(_("The requested dashboard can not be found."))

    render_dashboard(name)

def add_wato_folder_to_url(url, wato_folder):
    if not wato_folder:
        return url
    elif '/' in url:
        return url # do not append wato_folder to non-Check_MK-urls
    elif '?' in url:
        return url + "&wato_folder=" + html.urlencode(wato_folder)
    else:
        return url + "?wato_folder=" + html.urlencode(wato_folder)


# Actual rendering function
def render_dashboard(name):
    board = available_dashboards[name]

    # The dashboard may be called with "wato_folder" set. In that case
    # the dashboard is assumed to restrict the shown data to a specific
    # WATO subfolder or file. This could be a configurable feature in
    # future, but currently we assume, that *all* dashboards are filename
    # sensitive.

    wato_folder = html.var("wato_folder")

    # The title of the dashboard needs to be prefixed with the WATO path,
    # in order to make it clear to the user, that he is seeing only partial
    # data.
    title = _u(board["title"])

    global header_height
    if not board['show_title']:
        # Remove the whole header line
        html.set_render_headfoot(False)
        header_height = 0
        title = ''

    elif wato_folder is not None:
        title = wato.api.get_folder_title(wato_folder) + " - " + title

    html.header(title, javascripts=["dashboard"], stylesheets=["pages", "dashboard", "status", "views"])

    html.write("<div id=dashboard class=\"dashboard_%s\">\n" % name) # Container of all dashlets

    refresh_dashlets = [] # Dashlets with automatic refresh, for Javascript
    dashlets_js = []
    for nr, dashlet in enumerate(board["dashlets"]):
        # dashlets using the 'urlfunc' method will dynamically compute
        # an url (using HTML context variables at their wish).
        if "urlfunc" in dashlet:
            dashlet["url"] = dashlet["urlfunc"]()

        # dashlets using the 'url' method will be refreshed by us. Those
        # dashlets using static content (such as an iframe) will not be
        # refreshed by us but need to do that themselves.
        if "url" in dashlet or \
           ('type' in dashlet and 'render' in dashlets[dashlet['type']]
            and dashlets[dashlet['type']].get('refresh')):
            url = dashlet.get("url", "dashboard_dashlet.py?board="+name+"&id="+ \
                                     str(nr)+"&mtime="+str(board['mtime']));
            # FIXME: remove add_wato_folder_to_url
            refresh_dashlets.append([nr, dashlet.get("refresh", 0),
              str(add_wato_folder_to_url(url, wato_folder))])

        # Paint the dashlet's HTML code
        render_dashlet(nr, dashlet, wato_folder)

        dashlets_js.append({
            'x' : dashlet['position'][0],
            'y' : dashlet['position'][1],
            'w' : dashlet['size'][0],
            'h' : dashlet['size'][1],
        })

    if board['owner'] == config.user_id:
        html.write('<ul id="controls" style="display:none">\n')

        html.write('<li><a href="edit_dashboard.py?load_name=%s&back=%s">%s</a></li>\n' %
            (name, html.urlencode(html.makeuri([])), _('Properties')))

        display = html.var('edit') == '1' and 'block' or 'none'
        html.write('<li id="control_view" style="display:%s"><a href="javascript:void(0)" '
                   'onclick="toggle_dashboard_edit(false)">%s</a></li>\n' %
                        (display, _('View Dashboard')))

        display = html.var('edit') != '1' and 'block' or 'none'
        html.write('<li id="control_edit" style="display:%s"><a href="javascript:void(0)" '
                   'onclick="toggle_dashboard_edit(true)">%s</a></li>\n' %
                        (display, _('Edit Dashboard')))
        html.write("</ul>\n")

        html.icon_button(None, _('Edit the Dashboard'), 'dashboard_controls', 'controls_toggle',
                        onclick = 'toggle_dashboard_controls()')

    html.write("</div>\n")

    # Put list of all autorefresh-dashlets into Javascript and also make sure,
    # that the dashbaord is painted initially. The resize handler will make sure
    # that every time the user resizes the browser window the layout will be re-computed
    # and all dashlets resized to their new positions and sizes.
    html.javascript("""
var MAX = %d;
var GROW = %d;
var grid_size = new vec%s;
var header_height = %d;
var screen_margin = %d;
var title_height = %d;
var dashlet_padding = Array%s;
var corner_overlap = %d;
var refresh_dashlets = %r;
var dashboard_name = '%s';
var dashboard_mtime = %d;
var dashlets = %s;

calculate_dashboard();
window.onresize = function () { calculate_dashboard(); }
dashboard_scheduler(1);
    """ % (MAX, GROW, raster, header_height, screen_margin, title_height, dashlet_padding,
           corner_overlap, refresh_dashlets, name, board['mtime'], repr(dashlets_js)))

    if html.var('edit') == '1':
        html.javascript('toggle_dashboard_edit(true)')

    html.body_end() # omit regular footer with status icons, etc.

def render_dashlet_content(the_dashlet):
    dashlets[the_dashlet['type']]['render'](the_dashlet['parameters'])

# Create the HTML code for one dashlet. Each dashlet has an id "dashlet_%d",
# where %d is its index (in board["dashlets"]). Javascript uses that id
# for the resizing. Within that div there is an inner div containing the
# actual dashlet content. The margin between the inner and outer div is
# used for stylish layout stuff (shadows, etc.)
def render_dashlet(nr, dashlet, wato_folder):
    html.write('<div class=dashlet id="dashlet_%d">' % nr)
    # render shadow
    if dashlet.get("shadow", True):
        for p in [ "nw", "ne", "sw", "se", "n", "s", "w", "e" ]:
            html.write('<img id="dashadow_%s_%d" class="shadow %s" src="images/dashadow-%s.png">' %
                (p, nr, p, p))

    if dashlet.get("title"):
        url = dashlet.get("title_url", None)
        if url:
            title = '<a href="%s">%s</a>' % (url, dashlet["title"])
        else:
            title = dashlet["title"]
        html.write('<div class="title" id="dashlet_title_%d">%s</div>' % (nr, title))
    if dashlet.get("background", True):
        bg = " background"
    else:
        bg = ""
    html.write('<div class="dashlet_inner%s" id="dashlet_inner_%d">' % (bg, nr))

    dashlet_type = dashlets[dashlet['type']]

    # Optional way to render a dynamic iframe URL
    if "iframe_urlfunc" in dashlet_type:
        dashlet["iframe"] = dashlet_type["iframe_urlfunc"](dashlet['parameters'])

    # FIXME:
    if dashlet.get("reload_on_resize"):
        dashlet["onload"] = "dashlet_add_dimensions('dashlet_%d', this)" % nr

    # The content is rendered only if it is fixed. In the
    # other cases the initial (re)-size will paint the content.
    if "render" in dashlet_type:
        render_dashlet_content(dashlet)

    elif "content" in dashlet: # fixed content
        html.write(dashlet["content"])

    elif "iframe" in dashlet: # fixed content containing iframe
        # FIXME:
        if not dashlet.get("reload_on_resize"):
            url = add_wato_folder_to_url(dashlet["iframe"], wato_folder)
        else:
            url = 'about:blank'

        # Fix of iPad >:-P
        html.write('<div style="width: 100%; height: 100%; -webkit-overflow-scrolling:touch; overflow: hidden;">')
        html.write('<iframe id="dashlet_iframe_%d" allowTransparency="true" frameborder="0" width="100%%" '
                   'height="100%%" src="%s"> </iframe>' % (nr, url))
        html.write('</div>')
        # FIXME:
        if dashlet.get("reload_on_resize"):
            html.javascript('reload_on_resize["%d"] = "%s"' %
                            (nr, add_wato_folder_to_url(dashlet["iframe"], wato_folder)))
    html.write("</div></div>\n")

#.
#   .--Draw Dashlet--------------------------------------------------------.
#   |     ____                       ____            _     _      _        |
#   |    |  _ \ _ __ __ ___      __ |  _ \  __ _ ___| |__ | | ___| |_      |
#   |    | | | | '__/ _` \ \ /\ / / | | | |/ _` / __| '_ \| |/ _ \ __|     |
#   |    | |_| | | | (_| |\ V  V /  | |_| | (_| \__ \ | | | |  __/ |_      |
#   |    |____/|_|  \__,_| \_/\_/   |____/ \__,_|___/_| |_|_|\___|\__|     |
#   |                                                                      |
#   +----------------------------------------------------------------------+
#   | Draw dashlet HTML code which are rendered by the multisite dashboard |
#   '----------------------------------------------------------------------'

def ajax_dashlet():
    board = html.var('board')
    if not board:
        raise MKGeneralException(_('The name of the dashboard is missing.'))

    ident = html.var('id')
    if not ident:
        raise MKGeneralException(_('The ident of the dashlet is missing.'))

    load_dashboards()

    if board not in available_dashboards:
        raise MKGeneralException(_('The requested dashboard does not exist.'))
    dashboard = available_dashboards[board]

    mtime = html.var('mtime')
    if not mtime:
        raise MKGeneralException(_('The dashboard modification time is missing.'))

    if mtime < dashboard['mtime']:
        raise MKGeneralException('RELOAD') # FIXME

    the_dashlet = None
    for nr, dashlet in enumerate(dashboard['dashlets']):
        if nr == int(ident):
            the_dashlet = dashlet
            break

    if not the_dashlet:
        raise MKGeneralException(_('The dashlet can not be found on the dashboard.'))

    if the_dashlet['type'] not in dashlets:
        raise MKGeneralException(_('The requested dashlet type does not exist.'))

    render_dashlet_content(the_dashlet)

#.
#   .--Dashboard List------------------------------------------------------.
#   |           ____            _     _        _     _     _               |
#   |          |  _ \  __ _ ___| |__ | |__    | |   (_)___| |_             |
#   |          | | | |/ _` / __| '_ \| '_ \   | |   | / __| __|            |
#   |          | |_| | (_| \__ \ | | | |_) |  | |___| \__ \ |_             |
#   |          |____/ \__,_|___/_| |_|_.__(_) |_____|_|___/\__|            |
#   |                                                                      |
#   +----------------------------------------------------------------------+
#   |                                                                      |
#   '----------------------------------------------------------------------'

def page_edit_dashboards():
    def render_buttons():
        html.context_button(_("Create Dashboard"), "edit_dashboard.py", 'new')

    load_dashboards()
    visuals.page_list('dashboards', dashboards, render_context_buttons = render_buttons)

#.
#   .--Dashb. Config-------------------------------------------------------.
#   |     ____            _     _         ____             __ _            |
#   |    |  _ \  __ _ ___| |__ | |__     / ___|___  _ __  / _(_) __ _      |
#   |    | | | |/ _` / __| '_ \| '_ \   | |   / _ \| '_ \| |_| |/ _` |     |
#   |    | |_| | (_| \__ \ | | | |_) |  | |__| (_) | | | |  _| | (_| |     |
#   |    |____/ \__,_|___/_| |_|_.__(_)  \____\___/|_| |_|_| |_|\__, |     |
#   |                                                           |___/      |
#   +----------------------------------------------------------------------+
#   | Configures the global settings of a dashboard.                       |
#   '----------------------------------------------------------------------'

global vs_dashboard

def page_edit_dashboard():
    load_dashboards()

    global vs_dashboard
    vs_dashboard = Dictionary(
        title = _('Dashboard Properties'),
        render = 'form',
        optional_keys = None,
        elements = [
            ('show_title', Checkbox(
                title = _('Display dashboard title'),
                label = _('Show the header of the dashboard with the configured title.'),
                default_value = True,
            )),
        ],
    )
    visuals.page_edit_visual('dashboards', dashboards,
        create_handler = create_dashboard,
        custom_field_handler = custom_field_handler
    )

def custom_field_handler(dashboard):
    vs_dashboard.render_input('dashboard', dashboard and dashboard or None)

def create_dashboard(old_dashboard, dashboard):
    board_properties = vs_dashboard.from_html_vars('dashboard')
    vs_dashboard.validate_value(board_properties, 'dashboard')
    dashboard.update(board_properties)

    # Do not remove the dashlet configuration during general property editing
    dashboard['dashlets'] = old_dashboard.get('dashlets', [])
    dashboard['mtime'] = int(time.time())

    return dashboard
