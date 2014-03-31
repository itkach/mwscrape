# Copyright (C) 2014 Igor Tkach
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import couchdb
from urlparse import urlparse


def parse_args():
    argparser = argparse.ArgumentParser()
    argparser.add_argument('couch_url')
    argparser.add_argument('-s', '--start')
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


def main():
    args = parse_args()
    db = mkclient(args.couch_url)
    viewoptions = {}
    if args.start:
        viewoptions['startkey'] = args.start
        viewoptions['startkey_docid'] = args.start
    for row in db.iterview('_all_docs', 100, **viewoptions):
        doc = db.get(row.id, conflicts=True)
        conflicts = doc.get('_conflicts')
        if conflicts:
            best_mw_revid = doc['parse']['revid']
            docs = [doc]
            best_doc = doc
            print row.id, '\n', doc.rev, best_mw_revid, conflicts
            all_aliases = set(doc.get('aliases', ()))
            aliase_count = len(all_aliases)
            for conflict_rev in conflicts:
                conflict_doc = db.get(row.id, rev=conflict_rev)
                docs.append(conflict_doc)
                conflict_mw_revid = conflict_doc['parse']['revid']
                #print 'conflict mw revid:', conflict_mw_revid
                if conflict_mw_revid > best_mw_revid:
                    best_mw_revid = conflict_mw_revid
                    best_doc = conflict_doc
                aliases = set(doc.get('aliases', ()))
                all_aliases.update(aliases)
            #print all_aliases
            new_aliases_count = len(all_aliases) - aliase_count
            #print 'New aliases found in conflict:', new_aliases_count
            #print 'Best doc: ', best_doc.rev
            if new_aliases_count > 0:
                print '+A', doc.id
            if best_doc.rev != doc.rev > 0:
                print '+R', doc.id

            for doc in docs:
                if doc.rev == best_doc.rev:
                    print 'Keeping ', doc.rev
                    doc['aliases'] = list(all_aliases)
                    db.save(doc)
                else:
                    print 'Discarding ', doc.rev
                    db.delete(doc)


if __name__ == '__main__':
    main()
