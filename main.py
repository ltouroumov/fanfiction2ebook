import sys
import re
import requests
from os import getenv, remove, makedirs, path
from bs4 import BeautifulSoup
from ebooklib import epub
from threading import Thread


def and_then(opt, mapper):
    """
    None composition
    """
    if opt is None:
        return opt
    else:
        return mapper(opt)


def download_chapter(fid, ch):
    """
    Downloads a specific chapter from fic_id
    :param fid: Fanfiction ID
    :param ch: Chapter number
    :return: chapter contents
    """
    url = "https://www.fanfiction.net/s/%s/%s" % (fid, ch)
    resp = requests.get(url)
    if resp.status_code != 200:
        raise FileNotFoundError("Unable to download %s" % url)

    return BeautifulSoup(resp.text, "lxml")


def extract_header(fic_body):
    """
    Extracts the header of the fanfiction, returning:
        * Title
        * Author
        * Summary
        * Number of chapters
        * Infoline
    :param fic_body: bs4 object of a chapter page
    :return: Fic informations
    """
    heading_tag = fic_body.find('div', id='profile_top')
    if heading_tag is None:
        raise RuntimeError('Failed to find a <div id="profile_top">')

    fic_info = {'title': and_then(heading_tag.find('b', class_='xcontrast_txt'), lambda x: x.text),
                'author': and_then(heading_tag.find('a', class_='xcontrast_txt'), lambda x: x.text),
                'summary': and_then(heading_tag.find('div', class_='xcontrast_txt'), lambda x: x.text)}
    infoline = heading_tag.find('span', class_='xgray xcontrast_txt')
    if not infoline is None:
        key_whitelist = ('Words', 'Chapters', 'Status', 'Rated')
        for entry in infoline.text.split('-'):
            key, *val = map(lambda s: s.strip(), entry.split(':'))
            if key in key_whitelist:
                fic_info[key] = val[0]

    chapter_titles = {}
    if 'Chapters' in fic_info:
      chapter_titles_select = fic_body.find('select', id='chap_select')
      if chapter_titles_select is None:
          raise RuntimeError("Failed to find <select id=chap_select>")

      all_titles = chapter_titles_select.find_all('option')
      for item in all_titles:
          *_, name = item.text.split('.', 1)
          chapter_titles[int(item['value'])] = name
    else:
      fic_info['Chapters'] = '1'
      chapter_titles[1] = fic_info['title']
    
    fic_info['chapter_titles'] = chapter_titles
    return fic_info


def extract_chapter(fic_body, chapter_title):
    chapter_text = fic_body.find('div', id='storytext')
    if chapter_text is None:
        raise RuntimeError("Chapter has no contents")

    chapter_title_tag = fic_body.new_tag('h1')
    chapter_title_tag.string = chapter_title
    chapter_text.insert(0, chapter_title_tag)

    chapter_html = BeautifulSoup("""
    <html>
    <head>
        <title>...</title>
        <link rel="stylesheet" type="text/css" href="style/main.css" />
    </head>
    <body></body>
    </html>""", 'lxml')
    chapter_html.head.title.string = chapter_title
    chapter_html.body.append(chapter_text)

    return str(chapter_html)


def write_chapter(chapter_body, fid, cid):
    fname = path.join(fid, "%s.html" % cid)
    try:
        makedirs(path.dirname(fname))
    except FileExistsError:
        pass

    with open(fname, 'w+') as f:
        f.write(chapter_body)


def package_fanfic(fanfic_link):
    match = re.match(r"^https?://(www.)?fanfiction.net/s/(?P<id>\w+)/(?P<ch>\w+)/(?P<slug>.+)$", fanfic_link)
    if match is None:
        print("Impossible de récupérer les informations depuis l'URL: %s" % fanfic_link)
        exit(-1)

    fic_id, fic_slug = match.group('id', 'slug')

    out = OutStream(fic_slug)

    try:
        # Fetch
        out.print("Fetching metadata from first chapter ...")
        chapter_data = download_chapter(fic_id, 1)
        heading = extract_header(chapter_data)
        chapter_count = int(heading['Chapters'])
        out.print("FANFICTION: %s by %s, %s chapters" % (heading['title'], heading['author'], chapter_count))

        ebook = epub.EpubBook()
        ebook.set_identifier("fanfition-%s" % fic_id)
        ebook.set_title(heading['title'])
        ebook.add_author(heading['author'])
        doc_style = epub.EpubItem(
            uid="doc_style",
            file_name="style/main.css",
            media_type="text/css",
            content=open("style.css").read()
        )
        ebook.add_item(doc_style)

        intro_ch = epub.EpubHtml(title="Introduction", file_name='intro.xhtml')
        intro_ch.add_item(doc_style)
        intro_ch.content = """
        <html>
        <head>
            <title>Introduction</title>
            <link rel="stylesheet" href="style/main.css" type="text/css" />
        </head>
        <body>
            <h1>%s</h1>
            <p><b>By: %s</b></p>
            <p>%s</p>
        </body>
        </html>
        """ % (heading['title'], heading['author'], heading['summary'])
        ebook.add_item(intro_ch)

        chapters = []

        head_ch = epub.EpubHtml(title=heading['chapter_titles'][1], file_name='chapter_1.xhtml')
        head_ch.add_item(doc_style)
        head_ch.content = extract_chapter(chapter_data, head_ch.title)
        ebook.add_item(head_ch)
        chapters.append(head_ch)

        out.print("Downloading remaining chapters ...")
        for ch_id in range(2, chapter_count + 1):
            try:
                out.print("Downloading chapter %s" % ch_id)
                ch_title = heading['chapter_titles'][ch_id]
                chapter_file = path.join(fic_id, "%s.html" % ch_id)
                if path.exists(chapter_file) and USE_CACHE:
                    chapter_data = open(chapter_file).read()
                else:
                    chapter_data = download_chapter(fic_id, ch_id)
                    chapter_data = extract_chapter(chapter_data, ch_title)
                    if USE_CACHE:
                        write_chapter(chapter_data, fic_id, ch_id)

                ch = epub.EpubHtml(title=ch_title, file_name='chapter_%s.xhtml' % ch_id)
                ch.add_item(doc_style)
                ch.content = chapter_data
                ebook.add_item(ch)
                chapters.append(ch)
            except FileNotFoundError:
                out.print("Failed to fetch chapter")

        # Set the TOC
        ebook.toc = (
            epub.Link('intro.xhtml', 'Introduction', 'intro'),
            (epub.Section('Chapters'), chapters)
        )
        # add navigation files
        ebook.add_item(epub.EpubNcx())
        ebook.add_item(epub.EpubNav())


        # Create spine
        nav_page = epub.EpubNav(uid='book_toc', file_name='toc.xhtml')
        nav_page.add_item(doc_style)
        ebook.add_item(nav_page)
        ebook.spine = [intro_ch, nav_page] + chapters

        filename = '%s-%s.epub' % (fic_slug, fic_id)
        out.print("Saving to %s" % filename)
        if path.exists(filename):
            remove(filename)
        epub.write_epub(filename, ebook, {})

    except FileNotFoundError:
        out.print("Failed to fetch first chapter")
        exit(-1)


class FictionThread(Thread):
    def __init__(self, fanfic_link, **kwargs):
        super().__init__(**kwargs)
        self.fanfic_link = fanfic_link

    def run(self):
        package_fanfic(self.fanfic_link)


class OutStream:
    def __init__(self, name):
        self.name = name

    def print(self, str):
        try:
            print("[%s] %s" % (self.name, str))
        except:
            print("[%s] PC LOAD LETTER" % self.name)


if len(sys.argv) < 2:
    print("Nécéssite un ou plusieurs liens vers fanfiction.net!")
    exit(-1)

USE_CACHE = getenv('FF_CACHE', 'no') == 'yes'

# ex: https://www.fanfiction.net/s/10126177/1/Fratricidal
fanfic_links = sys.argv[1:]
threads = []
for link in fanfic_links:
    thread = FictionThread(link)
    thread.start()
    threads.append(thread)

while len(threads) > 0:
    head, *threads = threads
    head.join()

print("COMPLETE")
