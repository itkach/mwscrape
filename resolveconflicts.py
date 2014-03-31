# Copyright (C) 2014 Igor Tkach
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import couchdb

from urlparse import urlparse

from gevent import monkey; monkey.patch_all()
from gevent.pool import Pool


def parse_args():
    argparser = argparse.ArgumentParser()
    argparser.add_argument('couch_url')
    argparser.add_argument('-s', '--start')
    argparser.add_argument('-b', '--batch-size', type=int, default=10)
    argparser.add_argument('-v', '--verbose', action='store_true')
    return argparser.parse_args()


def mkclient(couch_url):
    parsed_url = urlparse(couch_url)
    couch_db = parsed_url.path.lstrip('/')
    server_url = parsed_url.scheme + '://'+ parsed_url.netloc
    server = couchdb.Server(server_url)
    username = parsed_url.username
    password = parsed_url.password
    print "User %s%s at %s, database %s" % (
        username,
        '' if password else ' (no password)',
        server.resource.url,
        couch_db)
    if password:
        server.resource.credentials = (username, password)
    return server[couch_db]


def resolve(db, doc_id, verbose=False):
    doc = db.get(doc_id, conflicts=True)
    conflicts = doc.get('_conflicts')
    messages = []
    if conflicts:
        best_mw_revid = doc['parse']['revid']
        docs = [doc]
        best_doc = doc
        all_aliases = set(doc.get('aliases', ()))
        aliase_count = len(all_aliases)
        article_revisions = set([best_mw_revid])
        for conflict_rev in conflicts:
            conflict_doc = db.get(doc_id, rev=conflict_rev)
            docs.append(conflict_doc)
            conflict_mw_revid = conflict_doc['parse']['revid']
            article_revisions.add(conflict_mw_revid)
            if conflict_mw_revid > best_mw_revid:
                best_mw_revid = conflict_mw_revid
                best_doc = conflict_doc
            aliases = set(doc.get('aliases', ()))
            all_aliases.update(aliases)
        new_aliases_count = len(all_aliases) - aliase_count
        article_rev_count = len(article_revisions) - 1
        if verbose:
            messages.append('------')
        messages.append(
            '%s [%d conflict(s): +%dr, +%da]' %
            (doc_id, len(conflicts), article_rev_count, new_aliases_count))
        for doc in docs:
            if doc.rev == best_doc.rev:
                if verbose:
                    messages.append('Keeping %s' % doc.rev)
                doc['aliases'] = list(all_aliases)
                db.save(doc)
            else:
                if verbose:
                    messages.append('Discarding %s' % doc.rev)
                db.delete(doc)
        result = True
    else:
        if verbose:
            messages.append('-')
        result = False
    if messages:
        print '\n'.join(messages)
    return result


def main():
    args = parse_args()
    db = mkclient(args.couch_url)
    viewoptions = {}
    if args.start:
        viewoptions['startkey'] = args.start
        viewoptions['startkey_docid'] = args.start

    pool = Pool(args.batch_size)
    for row in db.iterview('_all_docs', args.batch_size, **viewoptions):
        pool.spawn(resolve, db, row.id, verbose=args.verbose)
    pool.join()


if __name__ == '__main__':
    main()
