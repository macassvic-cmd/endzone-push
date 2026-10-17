import sys
d = open(sys.argv[1]).read(); r = open(sys.argv[2]).read() if len(sys.argv) > 2 else "null"
open("endzone_lab.html", "w").write(open("template.html").read().replace("__DATA__", d).replace("__RES__", r))
