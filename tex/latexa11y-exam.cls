%% latexa11y-exam.cls
%% Accessible exam class.
%%
%%   sol / prob      as for assignments
%%   inperson        print the name/SID block on the front page
%%   draft           downgrade unfilled-alt errors to warnings
%%
%% Exams are the hardest documents in a question bank to make accessible,
%% because their layout conventions carry meaning that exists only visually:
%% picture-mode boxes for written answers, \uline{\hfill} fill-in rules, and
%% blank pages inserted for working space. All three are provided here with
%% real semantics -- see \answerbox, \answerblank and \workingpage.

\NeedsTeXFormat{LaTeX2e}[2020-10-01]
\ProvidesClass{latexa11y-exam}[2026-08-08 v0.1.0 Accessible exam]

\newif\ifaccess@sol       \access@solfalse
\newif\ifaccess@ans       \access@ansfalse
\newif\ifaccess@guidance  \access@guidancefalse
\newif\ifaccess@inperson  \access@inpersonfalse
\newif\ifaccess@draft     \access@draftfalse

\DeclareOption{prob}{\access@solfalse\access@ansfalse}
\DeclareOption{sol}{\access@soltrue\access@anstrue}
\DeclareOption{ans}{\access@anstrue}
\DeclareOption{guidance}{\access@guidancetrue}
\DeclareOption{inperson}{\access@inpersontrue}
\DeclareOption{draft}{\access@drafttrue}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{article}}
\ProcessOptions\relax

\LoadClass[11pt]{article}

\RequirePackage[margin=1in]{geometry}
\RequirePackage{amsmath}
\RequirePackage{graphicx}
\RequirePackage{enumitem}
\RequirePackage{xcolor}
\RequirePackage{hyperref}
\RequirePackage{bookmark}
\hypersetup{hidelinks}

\RequirePackage{latexa11y-doc}

\ifaccess@sol\accessshowsolutions\fi
\ifaccess@ans\accessshowanswers\fi
\ifaccess@guidance\accessshowguidance\fi
\ifaccess@draft\accesssetup{strict=false}\fi

\ExplSyntaxOn

%% Candidate details. Each blank is a labelled region rather than a bare rule,
%% so a screen reader announces what belongs in it.
\NewDocumentCommand \candidatedetails { }
  {
    \par
    \accessheading{2}{ \bfseries Candidate~details }
    \begin{description}[leftmargin=0pt,style=nextline]
      \item[Full~name] \answerblank[8cm]
      \item[Student~ID] \answerblank[8cm]
      \item[Exam~room]  \answerblank[8cm]
    \end{description}
    \par
  }

%% A page left blank for working. It must be announced once, as a page for
%% working, and must not present a screen-reader user with silence.
\NewDocumentCommand \workingpage { }
  {
    \clearpage
    \altonly{ Blank~page~provided~for~your~working.~Nothing~on~this~page~is~
      part~of~the~question. }{ \strut }
    \clearpage
  }

%% Honour-code style statement that must be acknowledged in writing.
\NewDocumentCommand \signatureblock { m }
  {
    \par #1 \par
    \accessheading{3}{ \bfseries Signature }
    \answerbox[3\baselineskip]
  }

\ExplSyntaxOff
\endinput
