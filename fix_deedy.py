with open("templates/deedy_standard.tex", "r") as f:
    text = f.read()

text = text.replace("\\subsection{<< category.name >>]", "\\subsection{<< category.name >>}")
text = text.replace(" \\\\\n\\sectionsep", "\n\\sectionsep")

with open("templates/deedy_standard.tex", "w") as f:
    f.write(text)

try:
    with open("templates/deedy_reversed.tex", "r") as f:
        text2 = f.read()
    text2 = text2.replace(" \\\\\n\\sectionsep", "\n\\sectionsep")
    with open("templates/deedy_reversed.tex", "w") as f:
        f.write(text2)
except:
    pass
