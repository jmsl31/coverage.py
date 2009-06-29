/* C-based Tracer for Coverage. */

#include "Python.h"
#include "compile.h"        /* in 2.3, this wasn't part of Python.h */
#include "eval.h"           /* or this. */
#include "structmember.h"
#include "frameobject.h"

#undef WHAT_LOG     /* Define to log the WHAT params in the trace function. */
#undef TRACE_LOG    /* Define to log our bookkeeping. */

/* The Tracer type. */

typedef struct {
    PyObject_HEAD
    PyObject * should_trace;
    PyObject * data;
    PyObject * should_trace_cache;
    int started;
    /* The index of the last-used entry in tracenames. */
    int depth;
    /* Filenames to record at each level, or NULL if not recording. */
    PyObject ** tracenames;     /* PyMem_Malloc'ed. */
    int tracenames_alloc;       /* number of entries at tracenames. */
} Tracer;

#define TRACENAMES_DELTA    100

static int
Tracer_init(Tracer *self, PyObject *args, PyObject *kwds)
{
    self->should_trace = NULL;
    self->data = NULL;
    self->should_trace_cache = NULL;
    self->started = 0;
    self->depth = -1;
    self->tracenames = PyMem_Malloc(TRACENAMES_DELTA*sizeof(PyObject *));
    if (self->tracenames == NULL) {
        return -1;
    }
    self->tracenames_alloc = TRACENAMES_DELTA;
    return 0;
}

static void
Tracer_dealloc(Tracer *self)
{
    if (self->started) {
        PyEval_SetTrace(NULL, NULL);
    }

    Py_XDECREF(self->should_trace);
    Py_XDECREF(self->data);
    Py_XDECREF(self->should_trace_cache);

    while (self->depth >= 0) {
        Py_XDECREF(self->tracenames[self->depth]);
        self->depth--;
    }
    
    PyMem_Free(self->tracenames);

    self->ob_type->tp_free((PyObject*)self);
}

#if TRACE_LOG
static const char *
indent(int n)
{
    static const char * spaces = 
        "                                                                    "
        "                                                                    "
        "                                                                    "
        "                                                                    "
        ;
    return spaces + strlen(spaces) - n*2;
}

static int logging = 0;
/* Set these constants to be a file substring and line number to start logging. */
static const char * start_file = "tests/views";
static int start_line = 27;

static void
showlog(int depth, int lineno, PyObject * filename, const char * msg)
{
    if (logging) {
        printf("%s%3d ", indent(depth), depth);
        if (lineno) {
            printf("%4d", lineno);
        }
        else {
            printf("    ");
        }
        if (filename) {
            printf(" %s", PyString_AS_STRING(filename));
        }
        if (msg) {
            printf(" %s", msg);
        }
        printf("\n");
    }
}

#define SHOWLOG(a,b,c,d)    showlog(a,b,c,d)
#else
#define SHOWLOG(a,b,c,d)
#endif /* TRACE_LOG */

#if WHAT_LOG
static const char * what_sym[] = {"CALL", "EXC ", "LINE", "RET "};
#endif

static int
Tracer_trace(Tracer *self, PyFrameObject *frame, int what, PyObject *arg)
{
    PyObject * filename = NULL;
    PyObject * tracename = NULL;

    #if WHAT_LOG 
    if (what <= sizeof(what_sym)/sizeof(const char *)) {
        printf("trace: %s @ %s %d\n", what_sym[what], PyString_AS_STRING(frame->f_code->co_filename), frame->f_lineno);
    }
    #endif 

    #if TRACE_LOG
    if (strstr(PyString_AS_STRING(frame->f_code->co_filename), start_file) && frame->f_lineno == start_line) {
        logging = 1;
    }
    #endif

    switch (what) {
    case PyTrace_CALL:      /* 0 */
        self->depth++;
        if (self->depth >= self->tracenames_alloc) {
            /* We've outgrown our tracenames array: make it bigger. */
            int bigger = self->tracenames_alloc + TRACENAMES_DELTA;
            PyObject ** bigger_tracenames = PyMem_Realloc(self->tracenames, bigger * sizeof(PyObject *));
            if (bigger_tracenames == NULL) {
                self->depth--;
                return -1;
            }
            self->tracenames = bigger_tracenames;
            self->tracenames_alloc = bigger;
        }
        /* Check if we should trace this line. */
        filename = frame->f_code->co_filename;
        tracename = PyDict_GetItem(self->should_trace_cache, filename);
        if (tracename == NULL) {
            /* We've never considered this file before. */
            /* Ask should_trace about it. */
            PyObject * args = Py_BuildValue("(OO)", filename, frame);
            tracename = PyObject_Call(self->should_trace, args, NULL);
            Py_DECREF(args);
            if (tracename == NULL) {
                /* An error occurred inside should_trace. */
                return -1;
            }
            PyDict_SetItem(self->should_trace_cache, filename, tracename);
        }
        else {
            Py_INCREF(tracename);
        }

        /* If tracename is a string, then we're supposed to trace. */
        if (PyString_Check(tracename)) {
            self->tracenames[self->depth] = tracename;
            SHOWLOG(self->depth, frame->f_lineno, filename, "traced");
        }
        else {
            self->tracenames[self->depth] = NULL;
            Py_DECREF(tracename);
            SHOWLOG(self->depth, frame->f_lineno, filename, "skipped");
        }
        break;
    
    case PyTrace_RETURN:    /* 3 */
        if (self->depth >= 0) {
            SHOWLOG(self->depth, frame->f_lineno, frame->f_code->co_filename, "return");
            Py_XDECREF(self->tracenames[self->depth]);
            self->depth--;
        }
        break;
    
    case PyTrace_LINE:      /* 2 */
        if (self->depth >= 0) {
            SHOWLOG(self->depth, frame->f_lineno, frame->f_code->co_filename, "line");
            if (self->tracenames[self->depth]) {
                PyObject * t = PyTuple_New(2);
                tracename = self->tracenames[self->depth];
                Py_INCREF(tracename);
                PyTuple_SET_ITEM(t, 0, tracename);
                PyTuple_SET_ITEM(t, 1, PyInt_FromLong(frame->f_lineno));
                PyDict_SetItem(self->data, t, Py_None);
                Py_DECREF(t);
            }
        }
        break;
    }

    /* UGLY HACK: for some reason, pyexpat invokes the systrace function directly.
       It uses "pyexpat.c" as the filename, which is strange enough, but it calls
       it incorrectly: when an exception passes through the C code, it calls trace
       with an EXCEPTION, but never calls RETURN.  This throws off our bookkeeping.
       To make things right, if this is an EXCEPTION from pyexpat.c, then inject
       a RETURN event also.  
       
       I've reported the problem with pyexpat.c as http://bugs.python.org/issue6359 .
       If the bug in pyexpat.c gets fixed someday, we'll either have to put a 
       version check here, or do something more sophisticated to detect the 
       EXCEPTION-without-RETURN case that has to be fixed up.
    */
    if (what == PyTrace_EXCEPTION) {
        if (strstr(PyString_AS_STRING(frame->f_code->co_filename), "pyexpat.c")) {
            /* Stupid pyexpat: pretend it gave us the RETURN it should have. */
            SHOWLOG(self->depth, frame->f_lineno, frame->f_code->co_filename, "wrongexc");
            if (Tracer_trace(self, frame, PyTrace_RETURN, arg) < 0) {
                return -1;
            }
        }
    }

    return 0;
}

static PyObject *
Tracer_start(Tracer *self, PyObject *args)
{
    PyEval_SetTrace((Py_tracefunc)Tracer_trace, (PyObject*)self);
    self->started = 1;
    return Py_BuildValue("");
}

static PyObject *
Tracer_stop(Tracer *self, PyObject *args)
{
    if (self->started) {
        PyEval_SetTrace(NULL, NULL);
        self->started = 0;
    }
    return Py_BuildValue("");
}

static PyMemberDef
Tracer_members[] = {
    { "should_trace",       T_OBJECT, offsetof(Tracer, should_trace), 0,
            PyDoc_STR("Function indicating whether to trace a file.") },

    { "data",               T_OBJECT, offsetof(Tracer, data), 0,
            PyDoc_STR("The raw dictionary of trace data.") },

    { "should_trace_cache", T_OBJECT, offsetof(Tracer, should_trace_cache), 0,
            PyDoc_STR("Dictionary caching should_trace results.") },

    { NULL }
};

static PyMethodDef
Tracer_methods[] = {
    { "start",  (PyCFunction) Tracer_start, METH_VARARGS,
            PyDoc_STR("Start the tracer") },

    { "stop",   (PyCFunction) Tracer_stop,  METH_VARARGS,
            PyDoc_STR("Stop the tracer") },

    { NULL }
};

static PyTypeObject
TracerType = {
    PyObject_HEAD_INIT(NULL)
    0,                         /*ob_size*/
    "coverage.Tracer",         /*tp_name*/
    sizeof(Tracer),            /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)Tracer_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "Tracer objects",          /* tp_doc */
    0,                         /* tp_traverse */
    0,                         /* tp_clear */
    0,                         /* tp_richcompare */
    0,                         /* tp_weaklistoffset */
    0,                         /* tp_iter */
    0,                         /* tp_iternext */
    Tracer_methods,            /* tp_methods */
    Tracer_members,            /* tp_members */
    0,                         /* tp_getset */
    0,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    (initproc)Tracer_init,     /* tp_init */
    0,                         /* tp_alloc */
    0,                         /* tp_new */
};

/* Module definition */

void
inittracer(void)
{
    PyObject* mod;

    mod = Py_InitModule3("coverage.tracer", NULL, PyDoc_STR("Fast coverage tracer."));
    if (mod == NULL) {
        return;
    }

    TracerType.tp_new = PyType_GenericNew;
    if (PyType_Ready(&TracerType) < 0) {
        return;
    }

    Py_INCREF(&TracerType);
    PyModule_AddObject(mod, "Tracer", (PyObject *)&TracerType);
}
