%% latexa11y-exam.cls
%% Accessible exam class in the EECS 16A house style.
%%
%%   prob / sol / ans   as for assignments
%%   inperson           print the name/SID block on the front page
%%   housecolors        use the course's original solution colours
%%   draft              downgrade unfilled-alt errors to warnings
%%
%% Exams are the hardest documents in a question bank to make accessible,
%% because their layout conventions carry meaning that exists only visually:
%% picture-mode boxes for written answers, \uline{\hfill} fill-in rules, and
%% blank pages inserted for working space. All three are provided here with
%% real semantics -- see \answerbox, \answerblank and \workingpage.

\NeedsTeXFormat{LaTeX2e}[2020-10-01]
\ProvidesClass{latexa11y-exam}[2026-08-11 v0.2.0 Accessible exam]

\newif\ifaccess@sol       \access@solfalse
\newif\ifaccess@ans       \access@ansfalse
\newif\ifaccess@guidance  \access@guidancefalse
\newif\ifaccess@inperson  \access@inpersonfalse
\newif\ifaccess@draft     \access@draftfalse
\newif\ifaccess@house     \access@housefalse

\DeclareOption{prob}{\access@solfalse\access@ansfalse}
\DeclareOption{sol}{\access@soltrue\access@anstrue}
\DeclareOption{ans}{\access@anstrue}
\DeclareOption{guidance}{\access@guidancetrue}
\DeclareOption{inperson}{\access@inpersontrue}
\DeclareOption{housecolors}{\access@housetrue}
\DeclareOption{draft}{\access@drafttrue}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{article}}
\ProcessOptions\relax

\LoadClass[11pt]{article}

\RequirePackage{hyperref}
\RequirePackage{bookmark}

\ifaccess@house
  \PassOptionsToPackage{housecolors}{latexa11y-doc}
\fi
\RequirePackage{latexa11y-doc}

\ifaccess@sol\accessshowsolutions\fi
\ifaccess@ans\accessshowanswers\fi
\ifaccess@guidance\accessshowguidance\fi
\ifaccess@draft\accesssetup{strict=false}\fi

%% Candidate details. Each blank is a labelled region rather than a bare rule,
%% so a screen reader announces what belongs in it.
\newcommand{\candidatedetails}{%
  \par
  {\bf Print your name and student ID:}\par
  \smallskip
  Full name: \answerblank[8cm]\par
  Student ID: \answerblank[8cm]\par
  Exam room: \answerblank[8cm]\par
  \medskip
}

%% A page left blank for working. It must be announced once, as a page for
%% working, and must not present a screen-reader user with silence.
\newcommand{\workingpage}{%
  \clearpage
  \described{Blank page provided for your working. Nothing on this page is part
    of the question.}{\strut}%
  \clearpage
}

%% Honour-code style statement that must be acknowledged in writing.
\newcommand{\signatureblock}[1]{%
  \par #1 \par
  \answerbox[3\baselineskip]%
}

\endinput
