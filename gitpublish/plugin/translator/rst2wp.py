#! /usr/bin/env python
#
# Generate an HTML Snippet for WordPress Blogs from reStructuredText.
#
# This is a modification of the standard HTML writer that leaves out
# the header, the body tag, and several CSS classes that have no use
# in wordpress. What is left is an incomplete HTML document suitable
# for pasting into the WordPress online editor.
#
# Note: This is a quick hack, so it probably won't work for the more
#       advanced features of rst.
#
# Copyright (c) 2008 Matthias Friedrich <matt@mafr.de>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the Artistic License.
#
# Modified to support direct output of math and displaymath a la sphinx
# to WordPress, for rendering with jsMath: latex equations are
# embedded either in <span> (inline) or <div> (displaymath) for jsMath
# to render in the web browser.
# What this mainly required was converting single backslash
# to double backslash to correct for WP's stripping of single backslashes.
# -- CJL
import sys
import docutils
from docutils.writers import html4css1
from docutils import frontend, writers, nodes, utils
from docutils.core import publish_cmdline, default_description
from docutils.parsers.rst import directives, roles
from sphinx.ext.mathbase import MathDirective, math, eq_role, \
     displaymath
from sphinx.util.compat import directive_dwim


class MathDirective2(MathDirective):
	'removes one line from MathDirective that crashes'
	def run(self):
		latex = '\n'.join(self.content)
		if self.arguments and self.arguments[0]:
			latex = self.arguments[0] + '\n\n' + latex
		node = displaymath()
		node['latex'] = latex.replace('\\', '\\\\') # WP strips backslash
		node['label'] = self.options.get('label', None)
		node['nowrap'] = 'nowrap' in self.options
		ret = [node]
		if node['label']:
			tnode = nodes.target('', '', ids=['equation-' + node['label']])
			self.state.document.note_explicit_target(tnode)
			ret.insert(0, tnode)
		return ret


def math_role(role, rawtext, text, lineno, inliner, options={}, content=[]):
	latex = text.replace('\x00', '\\\\') # WP strips single backslash
	obj = math(latex=latex)
	obj.document = inliner.document # docutils crashes w/o this
	return [obj], []

def setup():
	'add support for math to docutils'
	nodes._add_node_class_names(['math', 'displaymath', 'eqref'])
	roles.register_local_role('math', math_role)
	roles.register_local_role('eq', eq_role)
	directives.register_directive('math', directive_dwim(MathDirective2))

class Writer(html4css1.Writer):
	supported = ('wphtml', )

	settings_spec = html4css1.Writer.settings_spec + ( )

	def __init__(self):
		html4css1.Writer.__init__(self)
		self.translator_class = WpHtmlTranslator


class WpHtmlTranslator(html4css1.HTMLTranslator):
	"""An HTML emitting visitor.

	Assumes your WP has support for jsMath."""

	doctype = ('')

	def __init__(self, *args):
		html4css1.HTMLTranslator.__init__(self, *args)
		self.stylesheet = [ ]
		self.meta = [ ]
		self.head = [ ]
		self.head_prefix = [ ]
		self.body_prefix = [ ]
		self.body_suffix = [ ]
		self.section_level = 3
		self.compact_simple = True
		self.literal_block = False


	def visit_document(self, node):
		pass

	def depart_document(self, node):
		pass

	def visit_section(self, node):
		self.section_level += 1

	def depart_section(self, node):
		self.section_level -= 1

	def visit_paragraph(self, node):
		if self.should_be_compact_paragraph(node):
			self.context.append('')
		else:
			self.body.append('')
			self.context.append('\n\n')

	def depart_paragraph(self, node):
		self.body.append(self.context.pop())

	def visit_reference(self, node):
		attrs = { }
		if node.has_key('refuri'):
			attrs['href'] = node['refuri']
		else:
			assert node.has_key('refid'), 'Invalid internal link'
			attrs['href'] = '#' + node['refid']
		self.body.append(self.starttag(node, 'a', '', **attrs))

	def visit_Text(self, node):
		if self.literal_block:
			text = node.astext()
		else:
			text = node.astext().replace('\n', ' ')
		encoded = self.encode(text)
		if self.in_mailto and self.settings.cloak_email_addresses:
			encoded = self.cloak_email(encoded)
		self.body.append(encoded)

	def visit_block_quote(self, node):
		self.body.append('\n')

	def depart_block_quote(self, node):
		self.body.append('\n')

	def visit_list_item(self, node):
		self.body.append('  ' + self.starttag(node, 'li', ''))

	def visit_title(self, node):
		h_level = self.section_level + self.initial_header_level - 1
		self.body.append(
			self.starttag(node, 'h%s' % h_level, '', **{ }))
		self.context.append('</h%s>\n\n' % (h_level, ))

	def depart_title(self, node):
		self.body.append(self.context.pop())

	def visit_literal_block(self, node):
		self.literal_block = True
		self.body.append(self.starttag(node, 'pre'))

	def depart_literal_block(self, node):
		self.body.append('\n</pre>\n\n')
		self.literal_block = False

	def visit_literal(self, node):
		self.body.append('<code>')

	def depart_literal(self, node):
		self.body.append('</code>')

	def visit_math(self, node):
		self.body.append(
			self.starttag(node, 'span', '', CLASS='math'))
		self.body.append(self.encode(node['latex']) + '</span>')
		raise nodes.SkipNode
		
	def visit_displaymath(self, node):
		self.body.append(self.starttag(node, 'div', CLASS='math'))
		self.body.append(node['latex'])
		self.body.append('</div>')
		raise nodes.SkipNode

		
if __name__ == '__main__':
	# docutils tries to load the module 'wphtml' below, so we need an alias
	sys.modules['wphtml'] = sys.modules['__main__']

	try:
	    import locale
	    locale.setlocale(locale.LC_ALL, '')
	except:
	    pass

	description = ('Generates an HTML Snippet for Wordpress from'
			'standalone reStructuredText sources.  '
			+ default_description)

	publish_cmdline(writer_name='wphtml', description=description)

# EOF