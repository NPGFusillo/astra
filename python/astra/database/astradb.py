import datetime
import json
import os
from functools import (lru_cache, cached_property)
from sdss_access import SDSSPath
from peewee import (SQL, fn, SqliteDatabase, BooleanField, IntegerField, AutoField, TextField, ForeignKeyField, DateTimeField, BigIntegerField, FloatField, BooleanField)
from sdssdb.connection import PeeweeDatabaseConnection
from sdssdb.peewee import BaseModel
from astra import (config, log)
from astra.utils import flatten
from astra import __version__
from tqdm import tqdm
from time import sleep
from importlib import import_module


# The database config should always be present, but let's not prevent importing the module because it's missing.
_database_config = config.get("astra_database", {})

try:
    # Environment variable overrides all, for testing purposes.
    _database_url = os.environ["ASTRA_DATABASE_URL"]
    if _database_url is not None:
        log.info(f"Using ASTRA_DATABASE_URL enironment variable")
except KeyError:
    _database_url = _database_config.get("url", None)

# If a URL is given, that overrides all other config settings.
if _database_url:
    from playhouse.db_url import connect
    database = connect(_database_url)
    schema = None

else:
    class AstraDatabaseConnection(PeeweeDatabaseConnection):
        dbname = _database_config.get("dbname", None)
        
    database = AstraDatabaseConnection(autoconnect=True)
    schema = _database_config.get("schema", None)

    profile = _database_config.get("profile", None)
    if profile is not None:
        try:
            database.set_profile(profile)
        except AssertionError as e:
            log.exception(e)
            log.warning(f"""
            Database profile '{profile}' set in Astra configuration file, but there is no database 
            profile called '{profile}' found in ~/.config/sdssdb/sdssdb.yml -- it should look like:
            
            {profile}:
                user: [USER]
                host: [HOST]
                port: 5432
                domain: [DOMAIN]

            See https://sdssdb.readthedocs.io/en/stable/intro.html#supported-profiles for more details. 
            If the profile name '{profile}' is incorrect, you can change the 'database' / 'profile' key 
            in ~/.astra/astra.yml
            """)


class AstraBaseModel(BaseModel):
    class Meta:
        database = database
        schema = schema


#from playhouse.sqlite_ext import JSONField
#from playhouse.postgres_ext import JSONField

class JSONField(TextField):
    def db_value(self, value):
        # Problems with querying, e.g. DataProduct.kwargs.contains("2M034") would be resolved to:
        #   ... ILIKE '"%2M034%"' 
        # and the extra quotation marks (from json.dumps) would kill us.
        #return json.dumps(value)
        # This change shouldn't screw anything up because we are usually just inserting/updating
        # with dicts, and not strings directly.
        if value is not None:
            return value if isinstance(value, str) else json.dumps(value)

    def python_value(self, value):
        if value is not None:
            return json.loads(value)

@lru_cache
def _lru_sdsspath(release):
    return SDSSPath(release=release)


class Source(AstraBaseModel):
    catalogid = BigIntegerField(primary_key=True)

    # TODO: Include things like Gaia / 2MASS photometry?
    # TODO: Do we even need these two?
    sdssv_target0 = BigIntegerField(null=True)
    sdssv_first_carton_name = TextField(null=True)

    @property
    def data_products(self):
        return (
            DataProduct.select()
                       .join(SourceDataProduct)
                       .join(Source)
                       .where(Source.catalogid == self.catalogid)
        )        


class DataProduct(AstraBaseModel):
    id = AutoField()
    release = TextField(default="sdss5") # TODO: Should we make this a configuration setting?
    filetype = TextField()
    kwargs = JSONField()

    metadata = JSONField(null=True)

    # A column that could be used to track n_visits, relative cost, etc.
    # Should only be used when comparing against DataProducts of the same
    # release and filetype
    size = IntegerField(null=True)

    class Meta:
        indexes = (
            # Always remember to put the comma at the end.
            (("release", "filetype", "kwargs"), True),
        )

    @property
    def input_to_tasks(self):
        return (
            Task.select()
                .join(TaskInputDataProducts)
                .join(DataProduct)
                .where(DataProduct.id == self.id)
        )

    @cached_property
    def path(self):
        kwds = self.kwargs.copy()
        if "field" in kwds:
            if kwds["field"].startswith(" "):
                log.warning(f"Field name of {self.release} {self.filetype} {self.kwargs} starts with spaces.")
                kwds["field"] = str(kwds["field"]).strip()
        return _lru_sdsspath(self.release).full(self.filetype, **kwds)
        

    @property
    def sources(self):
        return (
            Source.select()
                  .join(SourceDataProduct)
                  .join(DataProduct)
                  .where(DataProduct.id == self.id)
        )

# DataProducts and Sources should be a many-to-many relationship.
class SourceDataProduct(AstraBaseModel):
    id = AutoField()
    source = ForeignKeyField(Source)
    data_product = ForeignKeyField(DataProduct)

    class Meta:
        indexes = (
            # Always remember to put the comma at the end.
            (("source", "data_product"), True),
        )


class Output(AstraBaseModel):
    id = AutoField()
    created = DateTimeField(default=datetime.datetime.now)


class Status(AstraBaseModel):
    id = AutoField()
    description = TextField()

    class Meta:
        indexes = (
            # Always remember to put the comma at the end.
            (("id", "description"), True),
        )


class Task(AstraBaseModel):
    id = AutoField()
    name = TextField()
    parameters = JSONField(null=True)

    version = TextField()

    time_total = FloatField(null=True)
    time_pre_execute = FloatField(null=True)
    time_execute = FloatField(null=True)
    time_post_execute = FloatField(null=True)
    
    time_pre_execute_task = FloatField(null=True)
    time_pre_execute_bundle_overhead = FloatField(null=True)

    time_execute_task = FloatField(null=True)
    time_execute_bundle_overhead = FloatField(null=True)

    time_post_execute_task = FloatField(null=True)
    time_post_execute_bundle_overhead = FloatField(null=True)

    created = DateTimeField(default=datetime.datetime.now)
    completed = DateTimeField(null=True)

    status = ForeignKeyField(Status, default=1) # default: 1 is the lowest status level ('created' or similar)

    def as_executable(self, strict=True):
        log.warning(f"as_executable() deprecated -> instance")
        return self.instance()


    def instance(self, strict=True):
        """Return an executable representation of this task."""

        from astra.base import TaskInstance
        return TaskInstance.from_task(self, strict=strict)



    @property
    def input_data_products(self):
        return (
            DataProduct.select()
                       .join(TaskInputDataProducts)
                       .join(Task)
                       .where(Task.id == self.id)
        )

    @property
    def output_data_products(self):
        return (
            DataProduct.select()
                       .join(TaskOutputDataProducts)
                       .join(Task)
                       .where(Task.id == self.id)
        )


    @property
    def outputs(self):
        '''
        q = None
        # Create a compound union query to retrieve all possible outputs for this task.
        o = TaskOutput.get(TaskOutput.task == self)
        for expr, column in o.output.dependencies():
            if column.model != TaskOutput:def
                sq = column.model.select().where(column.model.task == self)
                if q is None:
                    q = sq
                else:
                    q += sq
        # Order by the order they were created.
        return q#.order_by(SQL("output_id").asc())
        '''
        outputs = []
        o = TaskOutput.get(TaskOutput.task == self)
        for expr, column in o.output.dependencies():
            if column.model != TaskOutput:
                outputs.extend(column.model.select().where(column.model.task == self))
        return sorted(outputs, key=lambda x: x.output_id)
    
    def count_outputs(self):
        return TaskOutput.select().where(TaskOutput.task == self).count()



class TaskOutput(AstraBaseModel):
    id = AutoField()
    task = ForeignKeyField(Task)
    output = ForeignKeyField(Output)


class Bundle(AstraBaseModel):
    id = AutoField()
    status = ForeignKeyField(Status, default=1) # default: 1 is the lowest status level ('created' or similar)
    meta = JSONField(null=True)

    @property
    def tasks(self):
        return (
            Task.select()
                .join(TaskBundle)
                .join(Bundle)
                .where(Bundle.id == self.id)
        )

    def _watch(self, interval=1):
        """
        Watch the progress of this bundle being executed (perhaps by some other executor).

        Progress is measured by the number of tasks with at least one TaskOutput, divided
        by the total number of tasks.
        """
        T = self.count_tasks()
        N = self.count_tasks_with_outputs()
        with tqdm(total=T, initial=N) as pb:
            while True:
                sleep(interval)
                M = self.count_tasks_with_outputs()
                if M > pb.n:
                    pb.update(M - pb.n)
                if M >= T:
                    break
        return None
                
    def count_tasks_with_outputs(self):
        return (
            TaskOutput.select()
                      .distinct(TaskOutput.task)
                      .where(TaskOutput.task.in_(self.tasks))
                      .count()
        )


    def count_tasks(self):
        return self.tasks.count()

    def count_input_data_products(self):
        return (
            DataProduct.select()
                       .join(TaskInputDataProducts)
                       .join(Task)
                       .join(TaskBundle)
                       .where(TaskBundle.bundle_id == self.id)
                       .count()
        )

    def count_input_data_products_size(self):
        count, = (
            DataProduct.select(fn.SUM(DataProduct.size))
                       .join(TaskInputDataProducts)
                       .join(Task)
                       .join(TaskBundle)
                       .join(Bundle)
                       .where(Bundle.id == self.id)
                       .tuples()
                       .first()
        )
        return count

    def as_executable(self):
        log.warning(f"as_executable() deprecated -> instance")
        return self.instance()

    def instance(self, strict=True):
        from astra.base import TaskInstance
        return TaskInstance.from_bundle(self, strict=strict)


class TaskBundle(AstraBaseModel):
    id = AutoField()
    task = ForeignKeyField(Task, on_delete="CASCADE")
    bundle = ForeignKeyField(Bundle, on_delete="CASCADE")


class TaskInputDataProducts(AstraBaseModel):
    id = AutoField()
    task = ForeignKeyField(Task, on_delete="CASCADE")
    data_product = ForeignKeyField(DataProduct, on_delete="CASCADE")
    

class TaskOutputDataProducts(AstraBaseModel):
    id = AutoField()
    task = ForeignKeyField(Task, on_delete="CASCADE")
    data_product = ForeignKeyField(DataProduct, on_delete="CASCADE")


# Output tables.
SMALL = -1e-20
class ClassifierOutput(AstraBaseModel):

    output = ForeignKeyField(Output, on_delete="CASCADE", primary_key=True)
    task = ForeignKeyField(Task)
    spectrum_pk = BigIntegerField(null=True)   # An optional primary key for the individual spectrum.

    dithered = BooleanField()

    snr = FloatField()
    p_cv = FloatField(default=0)
    lp_cv = FloatField(default=SMALL)
    p_fgkm = FloatField(default=0)
    lp_fgkm = FloatField(default=SMALL)
    p_hotstar = FloatField(default=0)
    lp_hotstar = FloatField(default=SMALL)
    p_wd = FloatField(default=0)
    lp_wd = FloatField(default=SMALL)
    p_sb2 = FloatField(default=0)
    lp_sb2 = FloatField(default=SMALL)
    p_yso = FloatField(default=0)
    lp_yso = FloatField(default=SMALL)


class ClassifySourceOutput(AstraBaseModel):

    output = ForeignKeyField(Output, on_delete="CASCADE", primary_key=True)
    task = ForeignKeyField(Task)
    spectrum_pk = BigIntegerField(null=True)   # An optional primary key for the individual spectrum.

    p_cv = FloatField(default=0)
    lp_cv = FloatField(default=SMALL)
    p_fgkm = FloatField(default=0)
    lp_fgkm = FloatField(default=SMALL)
    p_hotstar = FloatField(default=0)
    lp_hotstar = FloatField(default=SMALL)
    p_wd = FloatField(default=0)
    lp_wd = FloatField(default=SMALL)
    p_sb2 = FloatField(default=0)
    lp_sb2 = FloatField(default=SMALL)
    p_yso = FloatField(default=0)
    lp_yso = FloatField(default=SMALL)


class FerreOutput(AstraBaseModel):

    output = ForeignKeyField(Output, on_delete="CASCADE", primary_key=True)
    task = ForeignKeyField(Task)
    spectrum_pk = BigIntegerField(null=True)   # An optional primary key for the individual spectrum.

    snr = FloatField()
    teff = FloatField()
    logg = FloatField()
    metals = FloatField()
    lgvsini = FloatField(null=True)
    # BA grid doesn't use these:
    log10vdop = FloatField(null=True) 
    o_mg_si_s_ca_ti = FloatField(null=True)
    c = FloatField(null=True) 
    n = FloatField(null=True)

    u_teff = FloatField()
    u_logg = FloatField()
    u_metals = FloatField()
    u_log10vdop = FloatField(null=True)
    u_lgvsini = FloatField(null=True)
    u_o_mg_si_s_ca_ti = FloatField(null=True)
    u_c = FloatField(null=True)
    u_n = FloatField(null=True)

    bitmask_teff = IntegerField(default=0)
    bitmask_logg = IntegerField(default=0)
    bitmask_metals = IntegerField(default=0)
    bitmask_log10vdop = IntegerField(default=0)
    bitmask_lgvsini = IntegerField(default=0)
    bitmask_o_mg_si_s_ca_ti = IntegerField(default=0)
    bitmask_c = IntegerField(default=0)
    bitmask_n = IntegerField(default=0)   

    log_chisq_fit = FloatField()
    log_snr_sq = FloatField()
    frac_phot_data_points = FloatField(default=0)

    # This penalized log chisq term is strictly a term defined and used by ASPCAP
    # and not FERRE, but it is easier to understand what is happening when selecting
    # the `best` model if we have a penalized \chisq term.
    penalized_log_chisq_fit = FloatField(null=True)

    # Astra records the time taken *per task*, and infers things like overhead time for each stage
    # of pre_execute, execute, and post_execute.
    # But even one task with a single data model could contain many spectra that we analyse with
    # FERRE, and for performance purposes we want to know the time taken by FERRE.
    # For these reasons, let's store some metadata here, even if we could infer it from other things.
    ferre_time_elapsed = FloatField(null=True)
    ferre_time_load = FloatField(null=True)
    ferre_n_threads = IntegerField(null=True)
    ferre_n_obj = IntegerField(null=True)
    

class ApogeeNetOutput(AstraBaseModel):

    output = ForeignKeyField(Output, on_delete="CASCADE", primary_key=True)
    task = ForeignKeyField(Task)
    spectrum_pk = BigIntegerField(null=True)   # An optional primary key for the individual spectrum.

    snr = FloatField()
    teff = FloatField()
    logg = FloatField()
    fe_h = FloatField()
    u_teff = FloatField()
    u_logg = FloatField()
    u_fe_h = FloatField()
    teff_sample_median = FloatField()
    logg_sample_median = FloatField()
    fe_h_sample_median = FloatField()
    bitmask_flag = IntegerField(default=0)


class AspcapOutput(AstraBaseModel):

    output = ForeignKeyField(Output, on_delete="CASCADE", primary_key=True)
    task = ForeignKeyField(Task)
    spectrum_pk = BigIntegerField(null=True)   # An optional primary key for the individual spectrum.

    # Metadata.
    snr = FloatField()

# Dynamically add many fields to AspcapOutput.
sp_field_names = ("teff", "logg", "metals", "log10vdop", "o_mg_si_s_ca_ti", "lgvsini", "c", "n")
null_field_names = ("lgvsini", "log10vdop", "o_mg_si_s_ca_ti", "c", "n")
elements = (
    "cn", "al", "ca", "ce", "co", "cr", "fe", "k", "mg", "mn",
    "na", "nd", "ni", "o", "p", "rb", "si", "s", "ti", "v", "yb"
)

for field_name in sp_field_names:
    null = field_name in null_field_names
    AspcapOutput._meta.add_field(field_name, FloatField(null=null))
    AspcapOutput._meta.add_field(f"u_{field_name}", FloatField(null=null))
    AspcapOutput._meta.add_field(f"bitmask_{field_name}", IntegerField(default=0))

AspcapOutput._meta.add_field("log_chisq_fit", FloatField())
AspcapOutput._meta.add_field("log_snr_sq", FloatField())

# All element fields can be null, and they need their own log_chisq_fit 
for element in elements:
    AspcapOutput._meta.add_field(f"{element}_h", FloatField(null=True))
    AspcapOutput._meta.add_field(f"u_{element}_h", FloatField(null=True))
    AspcapOutput._meta.add_field(f"bitmask_{element}_h", IntegerField(default=0))
    AspcapOutput._meta.add_field(f"log_chisq_fit_{element}_h", FloatField(null=True))



class TheCannonOutput(AstraBaseModel):

    output = ForeignKeyField(Output, on_delete="CASCADE", primary_key=True)
    task = ForeignKeyField(Task)
    spectrum_pk = BigIntegerField(null=True)   # An optional primary key for the individual spectrum.

    # Metadata.
    snr = FloatField()
    bitmask_flag = IntegerField(default=0)
    chi_sq = FloatField()
    reduced_chi_sq = FloatField()

    teff = FloatField()
    u_teff = FloatField()
    logg = FloatField()
    u_logg = FloatField()
    fe_h = FloatField()
    u_fe_h = FloatField()
    c_h = FloatField()
    u_c_h =  FloatField()
    n_h = FloatField()
    u_n_h =  FloatField()
    o_h = FloatField()
    u_o_h =  FloatField()
    na_h = FloatField()
    u_na_h = FloatField()
    mg_h = FloatField()
    u_mg_h = FloatField()
    al_h = FloatField()
    u_al_h = FloatField()
    si_h = FloatField()
    u_si_h = FloatField()
    s_h = FloatField()
    u_s_h =  FloatField()
    k_h = FloatField()
    u_k_h =  FloatField()
    ca_h = FloatField()
    u_ca_h = FloatField()
    ti_h = FloatField()
    u_ti_h = FloatField()
    v_h = FloatField()
    u_v_h =  FloatField()
    cr_h = FloatField()
    u_cr_h = FloatField()
    mn_h = FloatField()
    u_mn_h = FloatField()
    co_h = FloatField()
    u_co_h = FloatField()
    ni_h = FloatField()
    u_ni_h = FloatField()

    rho_teff_logg = FloatField(default=0)
    rho_teff_fe_h = FloatField(default=0)
    rho_logg_fe_h = FloatField(default=0)
    rho_teff_c_h = FloatField(default=0)
    rho_logg_c_h = FloatField(default=0)
    rho_fe_h_c_h = FloatField(default=0)
    rho_teff_n_h = FloatField(default=0)
    rho_logg_n_h = FloatField(default=0)
    rho_fe_h_n_h = FloatField(default=0)
    rho_c_h_n_h = FloatField(default=0)
    rho_teff_o_h = FloatField(default=0)
    rho_logg_o_h = FloatField(default=0)
    rho_fe_h_o_h = FloatField(default=0)
    rho_c_h_o_h = FloatField(default=0)
    rho_n_h_o_h = FloatField(default=0)
    rho_teff_na_h = FloatField(default=0)
    rho_logg_na_h = FloatField(default=0)
    rho_fe_h_na_h = FloatField(default=0)
    rho_c_h_na_h = FloatField(default=0)
    rho_n_h_na_h = FloatField(default=0)
    rho_o_h_na_h = FloatField(default=0)
    rho_teff_mg_h = FloatField(default=0)
    rho_logg_mg_h = FloatField(default=0)
    rho_fe_h_mg_h = FloatField(default=0)
    rho_c_h_mg_h = FloatField(default=0)
    rho_n_h_mg_h = FloatField(default=0)
    rho_o_h_mg_h = FloatField(default=0)
    rho_na_h_mg_h = FloatField(default=0)
    rho_teff_al_h = FloatField(default=0)
    rho_logg_al_h = FloatField(default=0)
    rho_fe_h_al_h = FloatField(default=0)
    rho_c_h_al_h = FloatField(default=0)
    rho_n_h_al_h = FloatField(default=0)
    rho_o_h_al_h = FloatField(default=0)
    rho_na_h_al_h = FloatField(default=0)
    rho_mg_h_al_h = FloatField(default=0)
    rho_teff_si_h = FloatField(default=0)
    rho_logg_si_h = FloatField(default=0)
    rho_fe_h_si_h = FloatField(default=0)
    rho_c_h_si_h = FloatField(default=0)
    rho_n_h_si_h = FloatField(default=0)
    rho_o_h_si_h = FloatField(default=0)
    rho_na_h_si_h = FloatField(default=0)
    rho_mg_h_si_h = FloatField(default=0)
    rho_al_h_si_h = FloatField(default=0)
    rho_teff_s_h = FloatField(default=0)
    rho_logg_s_h = FloatField(default=0)
    rho_fe_h_s_h = FloatField(default=0)
    rho_c_h_s_h = FloatField(default=0)
    rho_n_h_s_h = FloatField(default=0)
    rho_o_h_s_h = FloatField(default=0)
    rho_na_h_s_h = FloatField(default=0)
    rho_mg_h_s_h = FloatField(default=0)
    rho_al_h_s_h = FloatField(default=0)
    rho_si_h_s_h = FloatField(default=0)
    rho_teff_k_h = FloatField(default=0)
    rho_logg_k_h = FloatField(default=0)
    rho_fe_h_k_h = FloatField(default=0)
    rho_c_h_k_h = FloatField(default=0)
    rho_n_h_k_h = FloatField(default=0)
    rho_o_h_k_h = FloatField(default=0)
    rho_na_h_k_h = FloatField(default=0)
    rho_mg_h_k_h = FloatField(default=0)
    rho_al_h_k_h = FloatField(default=0)
    rho_si_h_k_h = FloatField(default=0)
    rho_s_h_k_h = FloatField(default=0)
    rho_teff_ca_h = FloatField(default=0)
    rho_logg_ca_h = FloatField(default=0)
    rho_fe_h_ca_h = FloatField(default=0)
    rho_c_h_ca_h = FloatField(default=0)
    rho_n_h_ca_h = FloatField(default=0)
    rho_o_h_ca_h = FloatField(default=0)
    rho_na_h_ca_h = FloatField(default=0)
    rho_mg_h_ca_h = FloatField(default=0)
    rho_al_h_ca_h = FloatField(default=0)
    rho_si_h_ca_h = FloatField(default=0)
    rho_s_h_ca_h = FloatField(default=0)
    rho_k_h_ca_h = FloatField(default=0)
    rho_teff_ti_h = FloatField(default=0)
    rho_logg_ti_h = FloatField(default=0)
    rho_fe_h_ti_h = FloatField(default=0)
    rho_c_h_ti_h = FloatField(default=0)
    rho_n_h_ti_h = FloatField(default=0)
    rho_o_h_ti_h = FloatField(default=0)
    rho_na_h_ti_h = FloatField(default=0)
    rho_mg_h_ti_h = FloatField(default=0)
    rho_al_h_ti_h = FloatField(default=0)
    rho_si_h_ti_h = FloatField(default=0)
    rho_s_h_ti_h = FloatField(default=0)
    rho_k_h_ti_h = FloatField(default=0)
    rho_ca_h_ti_h = FloatField(default=0)
    rho_teff_v_h = FloatField(default=0)
    rho_logg_v_h = FloatField(default=0)
    rho_fe_h_v_h = FloatField(default=0)
    rho_c_h_v_h = FloatField(default=0)
    rho_n_h_v_h = FloatField(default=0)
    rho_o_h_v_h = FloatField(default=0)
    rho_na_h_v_h = FloatField(default=0)
    rho_mg_h_v_h = FloatField(default=0)
    rho_al_h_v_h = FloatField(default=0)
    rho_si_h_v_h = FloatField(default=0)
    rho_s_h_v_h = FloatField(default=0)
    rho_k_h_v_h = FloatField(default=0)
    rho_ca_h_v_h = FloatField(default=0)
    rho_ti_h_v_h = FloatField(default=0)
    rho_teff_cr_h = FloatField(default=0)
    rho_logg_cr_h = FloatField(default=0)
    rho_fe_h_cr_h = FloatField(default=0)
    rho_c_h_cr_h = FloatField(default=0)
    rho_n_h_cr_h = FloatField(default=0)
    rho_o_h_cr_h = FloatField(default=0)
    rho_na_h_cr_h = FloatField(default=0)
    rho_mg_h_cr_h = FloatField(default=0)
    rho_al_h_cr_h = FloatField(default=0)
    rho_si_h_cr_h = FloatField(default=0)
    rho_s_h_cr_h = FloatField(default=0)
    rho_k_h_cr_h = FloatField(default=0)
    rho_ca_h_cr_h = FloatField(default=0)
    rho_ti_h_cr_h = FloatField(default=0)
    rho_v_h_cr_h = FloatField(default=0)
    rho_teff_mn_h = FloatField(default=0)
    rho_logg_mn_h = FloatField(default=0)
    rho_fe_h_mn_h = FloatField(default=0)
    rho_c_h_mn_h = FloatField(default=0)
    rho_n_h_mn_h = FloatField(default=0)
    rho_o_h_mn_h = FloatField(default=0)
    rho_na_h_mn_h = FloatField(default=0)
    rho_mg_h_mn_h = FloatField(default=0)
    rho_al_h_mn_h = FloatField(default=0)
    rho_si_h_mn_h = FloatField(default=0)
    rho_s_h_mn_h = FloatField(default=0)
    rho_k_h_mn_h = FloatField(default=0)
    rho_ca_h_mn_h = FloatField(default=0)
    rho_ti_h_mn_h = FloatField(default=0)
    rho_v_h_mn_h = FloatField(default=0)
    rho_cr_h_mn_h = FloatField(default=0)
    rho_teff_co_h = FloatField(default=0)
    rho_logg_co_h = FloatField(default=0)
    rho_fe_h_co_h = FloatField(default=0)
    rho_c_h_co_h = FloatField(default=0)
    rho_n_h_co_h = FloatField(default=0)
    rho_o_h_co_h = FloatField(default=0)
    rho_na_h_co_h = FloatField(default=0)
    rho_mg_h_co_h = FloatField(default=0)
    rho_al_h_co_h = FloatField(default=0)
    rho_si_h_co_h = FloatField(default=0)
    rho_s_h_co_h = FloatField(default=0)
    rho_k_h_co_h = FloatField(default=0)
    rho_ca_h_co_h = FloatField(default=0)
    rho_ti_h_co_h = FloatField(default=0)
    rho_v_h_co_h = FloatField(default=0)
    rho_cr_h_co_h = FloatField(default=0)
    rho_mn_h_co_h = FloatField(default=0)
    rho_teff_ni_h = FloatField(default=0)
    rho_logg_ni_h = FloatField(default=0)
    rho_fe_h_ni_h = FloatField(default=0)
    rho_c_h_ni_h = FloatField(default=0)
    rho_n_h_ni_h = FloatField(default=0)
    rho_o_h_ni_h = FloatField(default=0)
    rho_na_h_ni_h = FloatField(default=0)
    rho_mg_h_ni_h = FloatField(default=0)
    rho_al_h_ni_h = FloatField(default=0)
    rho_si_h_ni_h = FloatField(default=0)
    rho_s_h_ni_h = FloatField(default=0)
    rho_k_h_ni_h = FloatField(default=0)
    rho_ca_h_ni_h = FloatField(default=0)
    rho_ti_h_ni_h = FloatField(default=0)
    rho_v_h_ni_h = FloatField(default=0)
    rho_cr_h_ni_h = FloatField(default=0)
    rho_mn_h_ni_h = FloatField(default=0)
    rho_co_h_ni_h = FloatField(default=0)


def create_tables(
        drop_existing_tables=False, 
        reuse_if_open=True,
        insert_status_rows=True,
    ):
    """ 
    Create all tables for the Astra database. 
    
    """

    log.info(f"Connecting to database to create tables.")
    database.connect(reuse_if_open=reuse_if_open)
    models = AstraBaseModel.__subclasses__()
    log.info(f"Tables ({len(models)}): {', '.join([model.__name__ for model in models])}")
    if drop_existing_tables:
        log.info(f"Dropping existing tables..")
        database.drop_tables(models)

    database.create_tables(models)

    # Put data in for Status
    if insert_status_rows:
        log.info(f"Inserting Status rows")
        # Note that the most important description here is the first one, which should be the
        # lowest level of the status hierarchy. This is because the default status for a Task
        # or Bundle is `id=1`, so whichever is the lowest level of the hierarchy.
        status_descriptions = [
            "created",
            "locked",
            "submitted",
            "running",
            "completed",
            "failed-pre-execution",
            "failed-execution",
            "failed-post-execution"
        ]
        with database.atomic():
            for description in status_descriptions:
                Status.create(description=description)

