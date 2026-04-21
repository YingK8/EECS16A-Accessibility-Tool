mostly for exams, they use 

\includegraphics[scale]{path/to/figure}

the alt text version is:
\includegraphics[alt={A line graph showing a steady upward trend in sales over the past year}]{my-tikz-graph.png}

for other assignements, the format is:

\begin{center}
    \begin{tikzpicture}
        \begin{axis}[height=3.5cm, width=5cm, ylabel={Gold Code}]
            \addplot+[only marks, ycomb, thick, blue]
            table {
                x y
                0 1
                1 1
                2 -1
                3 1
                4 -1
            };
            \addplot[black, thick] coordinates {(0, 0) (4, 0)};
        \end{axis}
    \end{tikzpicture}
\end{center}